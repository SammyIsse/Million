"""Opskrift-import og -moderation (docs/Features.md "Opskrift-import og
-matching system").

To indgange, kørt lokalt af udvikleren (ligesom updater.py/scrapers - intet
her kaldes fra en live edge-request):

  import_recipe_from_url(url)
      Admin-import: hent side -> schema.org Recipe JSON-LD, AI-fallback
      (Ollama) hvis JSON-LD mangler -> match ingredienser mod app_cache
      (recipe_matching.py) -> gem som status='approved' (den der kører
      scriptet har allerede kurateret url'en).

  moderate_pending_recipes()
      Bruger-indsendte opskrifter (submit_recipe-RPC, se
      scripts/supabase-recipes.sql) lander altid som 'pending', fordi Ollama
      kun kører lokalt (ingen Ollama i CI/edge, se scraper/ai_classifier.py) -
      der er intet AI-kvalitetstjek at køre synkront ved indsendelse. Dette
      gennemløb behandler kø'en: matcher ingredienser + kører AI-kvalitetstjek
      og auto-godkender kun ved høj tillid. Fail-safe er "bliv i pending til
      mennesket kigger på den", modsat ai_classifier.py's produkt-filter hvor
      fail-safe er at inkludere - en useriøs/spam-opskrift der vises offentligt
      er en anden slags fejl end en overset fødevare i et produktfilter.
"""

from __future__ import annotations

import json
import os
import re

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from app_support import DEFAULT_HTTP_HEADERS, logger
from recipe_matching import load_current_products, match_recipe_ingredients

_OLLAMA_MODEL = 'gemma3:4b'
_OLLAMA_URL = 'http://localhost:11434/api/generate'

# Under denne AI-kvalitetsscore (0-1) forbliver en bruger-indsendt opskrift
# 'pending' til manuel gennemgang i stedet for at blive auto-godkendt.
_AI_APPROVE_THRESHOLD = 0.75


def _get_supabase_client():
    """Samme env-fallback-mønster som updater.py's _get_supabase_client."""
    url = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = (
        os.getenv('DEPLOY_KEY')
        or os.getenv('SUPABASE_KEY')
        or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
    )
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# JSON-LD Recipe-udtræk
# ---------------------------------------------------------------------------

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def fetch_html(url: str, timeout: int = 15) -> str | None:
    try:
        resp = requests.get(url, timeout=timeout, headers=DEFAULT_HTTP_HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.error(f"Kunne ikke hente opskrift-URL {url}: {e}")
        return None


def _iter_jsonld_blocks(html: str):
    for match in _JSONLD_SCRIPT_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _find_recipe_node(data):
    """Recipe-objektet kan ligge direkte, i en liste eller under @graph."""
    if isinstance(data, dict):
        types = data.get('@type')
        types = [types] if isinstance(types, str) else (types or [])
        if 'Recipe' in types:
            return data
        if '@graph' in data:
            found = _find_recipe_node(data['@graph'])
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_recipe_node(item)
            if found:
                return found
    return None


def _jsonld_text(value) -> str:
    """Recipe-instructions-elementer kan være rene strenge eller
    HowToStep/HowToSection-objekter med et 'text'-felt."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get('text', '')).strip()
    return ''


def _parse_iso8601_duration_minutes(value) -> int | None:
    if not value or not isinstance(value, str):
        return None
    m = re.match(r'P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?', value)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    total = hours * 60 + minutes
    return total or None


def extract_jsonld_recipe(html: str) -> dict | None:
    """Første valg: schema.org Recipe JSON-LD (Google Recipe-kort-formatet)."""
    for block in _iter_jsonld_blocks(html):
        node = _find_recipe_node(block)
        if node:
            return node
    return None


def normalize_jsonld_recipe(node: dict, url: str) -> dict:
    name = node.get('name') or ''
    image = node.get('image')
    if isinstance(image, list):
        image = image[0] if image else ''
    if isinstance(image, dict):
        image = image.get('url', '')

    ingredients_raw = node.get('recipeIngredient') or node.get('ingredients') or []
    if isinstance(ingredients_raw, str):
        ingredients_raw = [ingredients_raw]

    instructions_raw = node.get('recipeInstructions') or []
    if isinstance(instructions_raw, str):
        instructions = [s.strip() for s in instructions_raw.split('\n') if s.strip()]
    else:
        instructions = [t for t in (_jsonld_text(step) for step in instructions_raw) if t]

    servings = node.get('recipeYield')
    if isinstance(servings, list):
        servings = servings[0] if servings else None
    servings_int = None
    if servings is not None:
        m = re.search(r'\d+', str(servings))
        if m:
            servings_int = int(m.group())

    author = node.get('author')
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get('name', '')

    return {
        'title': str(name).strip() or 'Unavngivet opskrift',
        'source_url': url,
        'source_name': str(author or '').strip(),
        'image_url': str(image or ''),
        'servings': servings_int,
        'total_time_minutes': _parse_iso8601_duration_minutes(node.get('totalTime')),
        'instructions': instructions,
        'ingredients': [str(i).strip() for i in ingredients_raw if str(i).strip()],
        'imported_via': 'jsonld',
    }


# ---------------------------------------------------------------------------
# AI-fallback-parsing (JSON-LD mangler) og AI-kvalitetstjek - begge kræver en
# lokal Ollama-instans (samme model som scraper/ai_classifier.py). Ingen af
# dem kan køre i CI/edge - se moduldokumentation øverst.
# ---------------------------------------------------------------------------

def _strip_html_for_ai(html: str, max_chars: int = 12000) -> str:
    text = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', html)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def _call_ollama(prompt: str, num_predict: int = 800) -> str | None:
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={
                'model': _OLLAMA_MODEL,
                'prompt': prompt,
                'stream': False,
                'options': {'temperature': 0, 'num_predict': num_predict},
            },
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()['response']
    except Exception as e:
        logger.error(f"Ollama-kald fejlede: {e}")
        return None


def _extract_json_object(text: str) -> dict | None:
    """Ollama kan omkranse JSON med prosa/kodeblokke - tag den yderste {...}."""
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


_AI_FALLBACK_PROMPT = """Du får den rå tekst fra en dansk opskrift-hjemmeside. Udtræk opskriften som JSON med PRÆCIS disse felter:
{{"title": "...", "servings": <tal eller null>, "total_time_minutes": <tal eller null>, "ingredients": ["2 dl mælk", ...], "instructions": ["trin 1", "trin 2", ...]}}

Kun JSON i svaret, ingen forklaring. Hvis siden ikke indeholder en opskrift, svar {{"title": null}}.

Sidetekst:
{page_text}
"""


def ai_fallback_extract_recipe(html: str, url: str) -> dict | None:
    page_text = _strip_html_for_ai(html)
    response = _call_ollama(_AI_FALLBACK_PROMPT.format(page_text=page_text))
    if not response:
        return None
    data = _extract_json_object(response)
    if not data or not data.get('title'):
        return None
    return {
        'title': str(data.get('title', '')).strip() or 'Unavngivet opskrift',
        'source_url': url,
        'source_name': '',
        'image_url': '',
        'servings': data.get('servings'),
        'total_time_minutes': data.get('total_time_minutes'),
        'instructions': [str(s).strip() for s in (data.get('instructions') or []) if str(s).strip()],
        'ingredients': [str(i).strip() for i in (data.get('ingredients') or []) if str(i).strip()],
        'imported_via': 'ai_fallback',
    }


_AI_QUALITY_PROMPT = """Vurdér om dette er en RIGTIG, brugbar madopskrift - ikke spam, pjat, reklame eller volapyk.
Titel: {title}
Ingredienser: {ingredients}
Trin: {instructions}

Svar KUN med ét tal mellem 0.0 og 1.0 (0 = tydeligvis ikke en rigtig opskrift, 1 = helt klart en rigtig, brugbar opskrift). Intet andet i svaret.
"""


def ai_quality_check(recipe: dict, ingredient_lines: list[str]) -> tuple[float | None, str]:
    """Returnerer (score 0-1 eller None ved fejl, korte AI-noter til recipes.ai_quality_notes)."""
    prompt = _AI_QUALITY_PROMPT.format(
        title=recipe.get('title', ''),
        ingredients='; '.join(ingredient_lines[:40]),
        instructions=' '.join(str(s) for s in (recipe.get('instructions') or []))[:2000],
    )
    response = _call_ollama(prompt, num_predict=10)
    if response is None:
        return None, 'AI-kvalitetstjek fejlede (Ollama utilgængelig)'
    m = re.search(r'\d+\.?\d*', response)
    if not m:
        return None, f'AI svarede uventet: {response.strip()[:200]}'
    try:
        score = max(0.0, min(1.0, float(m.group())))
    except ValueError:
        return None, f'AI svarede uventet: {response.strip()[:200]}'
    return score, f'AI-kvalitetsscore {score:.2f}'


# ---------------------------------------------------------------------------
# Admin-import fra URL
# ---------------------------------------------------------------------------

def import_recipe_from_url(url: str) -> int | None:
    client = _get_supabase_client()
    if client is None:
        logger.error('Supabase-forbindelse mangler - kan ikke importere opskrift')
        return None

    html = fetch_html(url)
    if html is None:
        return None

    node = extract_jsonld_recipe(html)
    recipe = normalize_jsonld_recipe(node, url) if node else None
    if not recipe or not recipe['ingredients']:
        recipe = ai_fallback_extract_recipe(html, url)
    if not recipe or not recipe['ingredients']:
        logger.error(f'Kunne hverken finde JSON-LD eller AI-udtrække en opskrift fra {url}')
        return None

    products = load_current_products(client)
    matched = match_recipe_ingredients(
        [{'raw_text': line} for line in recipe['ingredients']], products,
    )

    try:
        result = client.table('recipes').insert({
            'source_url': recipe['source_url'],
            'source_name': recipe['source_name'],
            'title': recipe['title'],
            'image_url': recipe['image_url'],
            'servings': recipe['servings'],
            'total_time_minutes': recipe['total_time_minutes'],
            'instructions': recipe['instructions'],
            'imported_via': recipe['imported_via'],
            'status': 'approved',
            'approved_at': 'now()',
        }).execute()
        recipe_id = result.data[0]['id']

        rows = [{
            'recipe_id': recipe_id,
            'position': i,
            'raw_text': ing['raw_text'],
            'quantity': ing['quantity'],
            'unit': ing['unit'],
            'ingredient_name': ing['ingredient_name'],
            'matched_product_id': ing['matched_product_id'],
            'match_confidence': ing['match_confidence'],
            'match_method': ing['match_method'],
        } for i, ing in enumerate(matched)]
        client.table('recipe_ingredients').insert(rows).execute()
    except Exception as e:
        logger.error(f'Kunne ikke gemme opskrift fra {url}: {e}')
        return None

    matched_count = sum(1 for m in matched if m['matched_product_id'])
    logger.info(
        f"Importeret '{recipe['title']}' ({recipe['imported_via']}): "
        f"{matched_count}/{len(matched)} ingredienser matchet"
    )
    return recipe_id


# ---------------------------------------------------------------------------
# Moderation af bruger-indsendte opskrifter (submit_recipe-RPC)
# ---------------------------------------------------------------------------

def moderate_pending_recipes() -> None:
    """Behandl status='pending' opskrifter: match ingredienser, kør AI-
    kvalitetstjek, auto-godkend ved høj tillid. Kør lokalt/manuelt med jævne
    mellemrum (ingen Ollama i CI/edge - se moduldokumentation øverst)."""
    client = _get_supabase_client()
    if client is None:
        logger.error('Supabase-forbindelse mangler - kan ikke moderere opskrifter')
        return

    pending = (
        client.table('recipes')
        .select('id,title,instructions')
        .eq('status', 'pending')
        .is_('ai_quality_score', 'null')
        .execute()
    ).data or []
    if not pending:
        logger.info('Ingen ventende opskrifter til moderation')
        return

    products = load_current_products(client)

    for recipe in pending:
        recipe_id = recipe['id']
        ingredients = (
            client.table('recipe_ingredients')
            .select('id,raw_text')
            .eq('recipe_id', recipe_id)
            .order('position')
            .execute()
        ).data or []
        if not ingredients:
            continue

        matched = match_recipe_ingredients(ingredients, products)
        for ing in matched:
            client.table('recipe_ingredients').update({
                'quantity': ing['quantity'],
                'unit': ing['unit'],
                'ingredient_name': ing['ingredient_name'],
                'matched_product_id': ing['matched_product_id'],
                'match_confidence': ing['match_confidence'],
                'match_method': ing['match_method'],
            }).eq('id', ing['id']).execute()

        score, notes = ai_quality_check(recipe, [ing['raw_text'] for ing in ingredients])
        update = {'ai_quality_score': score, 'ai_quality_notes': notes}
        if score is not None and score >= _AI_APPROVE_THRESHOLD:
            update['status'] = 'approved'
            update['approved_at'] = 'now()'
            logger.info(f"Opskrift #{recipe_id} '{recipe['title']}' auto-godkendt (score {score:.2f})")
        else:
            logger.info(
                f"Opskrift #{recipe_id} '{recipe['title']}' forbliver 'pending' "
                f"(score {score}) - kræver manuel gennemgang"
            )
        client.table('recipes').update(update).eq('id', recipe_id).execute()


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'moderate':
        moderate_pending_recipes()
    elif len(sys.argv) > 1:
        import_recipe_from_url(sys.argv[1])
    else:
        print('Brug: python recipe_importer.py <url>  ELLER  python recipe_importer.py moderate')
