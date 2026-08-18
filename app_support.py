"""Shared utilities: logging, rate limiting, search index, optional DB flag."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime
from functools import lru_cache, wraps
from typing import Callable

try:
    from rapidfuzz.fuzz import ratio as rapid_ratio, token_sort_ratio as rapid_token_sort
except ImportError:
    from difflib import SequenceMatcher

    def rapid_ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0

    def rapid_token_sort(a: str, b: str) -> float:
        sa = ' '.join(sorted(a.split()))
        sb = ' '.join(sorted(b.split()))
        return SequenceMatcher(None, sa, sb).ratio() * 100.0

logger = logging.getLogger('million')

_db_available: bool | None = None


def configure_logging() -> None:
    level = logging.DEBUG if os.environ.get('FLASK_DEBUG', '0') == '1' else logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        )


def is_price_db_enabled() -> bool:
    flag = os.environ.get('ENABLE_PRICE_DB', 'auto').lower()
    if flag in ('0', 'false', 'no', 'off'):
        return False
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    return True


def set_db_available(ok: bool) -> None:
    global _db_available
    _db_available = ok


def db_available() -> bool:
    if _db_available is None:
        return is_price_db_enabled()
    return _db_available


class RateLimiter:
    """In-memory per-IP rate limit (no database).

    Prunes expired hits for the requested key on every call, and periodically
    sweeps out keys whose deque has emptied entirely - otherwise every unique
    key ever seen (one per IP+endpoint combo) would stay in memory forever,
    growing unbounded over the process lifetime."""

    def __init__(self, max_calls: int = 60, window_seconds: int = 60,
                 env_var: str | None = None):
        self._max_calls = max_calls
        self._env_var = env_var
        self._max_calls_resolved = env_var is None
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.time()

    @property
    def max_calls(self) -> int:
        """Graensen, oploest ved FOERSTE brug - ikke ved import.

        wrangler.toml's [vars] ligger ikke i os.environ paa det tidspunkt
        modulet importeres i Cloudflares Python-runtime; det er netop derfor
        src/worker.py saetter CLOUDFLARE_WORKERS manuelt foer app importeres.
        Laeses vaerdien i __init__, faar man derfor altid standarden."""
        if not self._max_calls_resolved:
            self._max_calls_resolved = True
            try:
                value = int(os.environ.get(self._env_var or '', '') or 0)
                if value >= 1:
                    self._max_calls = value
            except (TypeError, ValueError):
                pass
        return self._max_calls

    def _sweep_stale(self, now: float) -> None:
        stale = []
        for k, hits in self._hits.items():
            while hits and now - hits[0] >= self.window_seconds:
                hits.popleft()
            if not hits:
                stale.append(k)
        for k in stale:
            del self._hits[k]
        self._last_sweep = now

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(key, deque(maxlen=self.max_calls))
            while hits and now - hits[0] >= self.window_seconds:
                hits.popleft()
            allowed = len(hits) < self.max_calls
            if allowed:
                hits.append(now)
            if now - self._last_sweep >= self.window_seconds:
                self._sweep_stale(now)
            return allowed


# 60/min pr. IP er standarden og den eneste vaerdi produktionen nogensinde
# koerer med - build-pages.sh saetter kun API_RATE_LIMIT_PER_MIN i
# staging-bygget. Varen findes udelukkende for at kunne KAPACITETSMAALE: en
# load-test kommer fra én IP og bliver bremset her laenge foer serveren er
# presset, saa man ender med at maale sin egen ip-kvote i stedet for sitets
# kapacitet (set 2026-07-24, hvor alle "fejl" ved 30 brugere var 429).
api_limiter = RateLimiter(max_calls=60, window_seconds=60,
                          env_var='API_RATE_LIMIT_PER_MIN')

# Strammere limit på cart-event end den generelle API-grænse: uden den kunne
# én IP puste et enkelt produkts cart_popularity kunstigt op med gentagne
# kald. Anon-nøglen har IKKE direkte INSERT/UPDATE på tabellen (fjernet af
# scripts/supabase-hardening.sql) - al skrivning går gennem
# record_cart_activity/increment_cart_count(s), SECURITY DEFINER-RPC'er - men
# de RPC'er håndhæver ikke i sig selv nogen rate limit pr. IP, så uden denne
# grænse kunne samme IP stadig spamme dem.
cart_event_limiter = RateLimiter(max_calls=20, window_seconds=60)


def _client_ip() -> str:
    """Bedste bud på klientens rigtige IP - modstandsdygtig over for spoofing.

    CF-Connecting-IP sættes af Cloudflare selv (overskriver altid en evt.
    klient-sendt værdi af samme navn), så den kan ikke forfalskes når appen
    kører bag Cloudflare. Uden Cloudflare foran bruges X-Forwarded-For's
    SIDSTE led (tilføjet af den nærmeste proxy) i stedet for det første
    (klient-kontrolleret og dermed frit forfalskeligt - ellers kan enhver
    omgå rate-limiten ved bare at sende en ny værdi pr. request)."""
    from flask import request
    cf_ip = request.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[-1].strip()
    return request.remote_addr or 'unknown'


# Aggregeret taeller for afviste requests - IKKE en log-linje pr. request.
#
# Her stod tidligere `logger.warning('Rate limit exceeded for %s', key)`, som
# brød den ufravigelige regel i CLAUDE.md: "Lav aldrig noget der logger pr.
# request." Under et angreb producerede hver eneste blokeret request en
# log-linje med klientens IP - altsaa blev beskyttelsen sin egen forstaerker,
# praecis den fejlklasse der vaeltede produktionen 2026-07-19. Sekundaert
# havnede IP-adresser (persondata) i loggen.
#
# Samme moenster som src/worker.py::_sec_note/_sec_flush: taeller i hukommelsen,
# skyl hoejst én linje pr. minut pr. isolate, og log kun ANTAL og hvilke
# endpoints - aldrig IP'en. En stille afvisning uden noget signal overhovedet
# ville goere en fejlkonfigureret graense usynlig.
_RL_FLUSH_INTERVAL = 60.0
_rl_counts: dict = {}
_rl_last_flush = 0.0
_rl_lock = threading.Lock()


def _note_rate_limited(endpoint: str) -> None:
    """Taell én afvisning; skyl hoejst 1x/minut. Ingen IP, ingen pr.-request-I/O."""
    global _rl_last_flush
    now = time.time()
    with _rl_lock:
        _rl_counts[endpoint] = _rl_counts.get(endpoint, 0) + 1
        if now - _rl_last_flush < _RL_FLUSH_INTERVAL:
            return
        snapshot = dict(_rl_counts)
        _rl_counts.clear()
        _rl_last_flush = now
    total = sum(snapshot.values())
    logger.warning(
        'Rate limit: %d afviste requests sidste minut (%s)',
        total,
        ', '.join(f'{k}={v}' for k, v in sorted(snapshot.items(), key=lambda x: -x[1])[:5]),
    )


def rate_limit(limiter: RateLimiter) -> Callable:
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import jsonify
            key = f'{_client_ip()}:{f.__name__}'
            if not limiter.allow(key):
                _note_rate_limited(f.__name__)
                return jsonify(success=False, error='For mange forespørgsler. Prøv igen om lidt.'), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator


# æ og ø overlever NFKD-normaliseringen i normalize_name (de dekomponerer
# ikke), mens å bliver til "a". Skriver brugeren derfor "maelk" eller "oel" -
# helt almindeligt på et ikke-dansk tastatur, eller bare i hastværk - ramte
# søgningen INTET: "maelk" gav 0 af 388 mulige træf, uden så meget som et
# "mente du"-forslag. Foldningen sker på BEGGE sider af sammenligningen, så
# den kun kan tilføje match, aldrig fjerne et der virkede før. Bevidst kun
# her i søgestien: normalize_name selv bruges også af matchmotoren i
# updater.py, hvor en ændring ville flytte produktgrupperinger.
_ASCII_FOLD = str.maketrans({'æ': 'ae', 'ø': 'oe'})


def _fold(s: str) -> str:
    """Dansk→ASCII-folding til søgning. 'mælk' og 'maelk' bliver samme streng."""
    return s.translate(_ASCII_FOLD)


def _token_matches_term(token: str, term: str) -> bool:
    """Match hele tokens / præfiks / sammensætninger - ikke midt i et andet ord.

    "øl" matcher "øl" og "ølflaske" og "juleøl", men ikke "pølser".
    "ris" matcher "ris" og "rispapir", men ikke "gris" (for kort stamme).
    "maelk" matcher "mælk" - se _fold ovenfor.
    """
    if not token or not term:
        return False
    if 'æ' in token or 'ø' in token or 'æ' in term or 'ø' in term:
        folded_token, folded_term = _fold(token), _fold(term)
        if (folded_token, folded_term) != (token, term):
            return _token_matches_term(folded_token, folded_term)
    if token == term or token.startswith(term):
        return True
    # Sammensætning hvor søgeordet er slutningen (juleøl, flødeost).
    # Kræv mindst 3 tegn stamme, så "ris" ikke matcher "gris".
    if token.endswith(term) and len(token) - len(term) >= 3:
        return True
    # Omvendt præfiks: søgeordet udvider et trunkeret produkt-token (fx "hyldebl" ↔ "hyldeblomst")
    return len(token) >= 4 and term.startswith(token)


def _field_matches_term(field: str, term: str) -> bool:
    """True hvis et token i det normaliserede felt matcher term."""
    return any(_token_matches_term(tok, term) for tok in field.split())


def search_match_score(product: dict, query: str) -> int:
    """Højere = bedre relevans. Bruges til at sortere autocomplete/søgeresultater."""
    terms = [t for t in normalize_name(query).split() if t]
    if not terms:
        return 0
    name = normalize_name(str(product.get('name', '')))
    brand = normalize_name(str(product.get('brand', '')))
    # Samme folding som _token_matches_term, så "maelk" ikke bare FINDER de
    # rigtige varer, men også scorer dem som et rigtigt match.
    terms = [_fold(t) for t in terms]
    tokens = [_fold(t) for t in (name + ' ' + brand).split()]
    score = 0
    for term in terms:
        best = 0
        for tok in tokens:
            if tok == term:
                best = max(best, 100)
            elif tok.startswith(term):
                best = max(best, 70)
            elif tok.endswith(term) and len(tok) - len(term) >= 3:
                best = max(best, 50)
        score += best

    # Kategori-prior: et søgeord som "mælk" beskriver en VARETYPE, og den type
    # hører til en bestemt kategori. Uden dette afgjorde tiebreakeren (korteste
    # navn) rækkefølgen, og de syv første træf på "mælk" var chokolade
    # ("Marabou Mælk", "Toblerone Mælk", "HAVREKIKS, MÆLK") - ægte mælk lå
    # nummer otte. Alle var eksakte token-match, så scoren var identisk, og
    # "Marabou Mælk" er tilfældigvis et kortere navn end "Frisk Dansk Mælk".
    #
    # Prioren er ikke en ny håndlavet ordliste: den genbruger den kuraterede
    # term→kategori-tabel, unify_category allerede bruger til at placere varer
    # (_BILKA_CATEGORY_RULES). Er søgeordet dér knyttet til en kategori, får
    # varer i netop den kategori et løft - men KUN som tiebreak mellem varer
    # der i forvejen matcher lige godt på navnet. Et match i den forkerte
    # kategori bliver aldrig sorteret væk, kun placeret efter.
    expected = _search_term_category(tuple(terms))
    if expected and str(product.get('category', '')) == expected:
        score += 40

    # Kortere navne først ved lige score (mere specifik titel)
    return score * 1000 - min(len(name), 999)


def build_search_index(products: list, normalize_fn, flavor_fn=None) -> dict[str, set[str]]:
    """token -> set of product ids for fast AND-search."""
    index: dict[str, set[str]] = {}
    for product in products:
        pid = str(product.get('/product/id', '')).strip()
        if not pid or pid in ('None', ''):
            continue
        img = str(product.get('/product/imageLink', ''))
        text = ' '.join([
            str(product.get('/product/title', '')),
            str(product.get('/product/brand', '')),
            str(product.get('/product/description', '')),
        ])
        if flavor_fn:
            try:
                flavors = flavor_fn(text, img) if callable(flavor_fn) else ''
                if flavors:
                    text = f"{text} {flavors}"
            except Exception:
                pass
        norm = normalize_fn(text)
        seen_tokens: set[str] = set()
        for token in norm.split():
            # >= 2 så korte søgninger som "øl" / "is" selv indekseres
            if len(token) >= 2 and token not in seen_tokens:
                seen_tokens.add(token)
                index.setdefault(token, set()).add(pid)
    return index


def search_product_ids(index: dict[str, set[str]], query: str) -> set[str] | None:
    # Samme normalisering som indeksets tokens (bygget med normalize_name i
    # updater.py) - ellers matcher en søgning på "hk" aldrig et indeks-token
    # "hakket" (og omvendt), fordi normaliseringen kun sker i én retning.
    terms = [t for t in normalize_name(query).split() if len(t) >= 2]
    if not terms or not index:
        return None
    result: set[str] | None = None
    for term in terms:
        # Ældre caches indekserede kun tokens >= 3 tegn. Korte termer uden
        # exact key kan derfor mangle ægte hits - fald tilbage til linear scan.
        if len(term) < 3 and term not in index:
            return None
        term_ids: set[str] = set()
        for token, pids in index.items():
            if _token_matches_term(token, term):
                term_ids.update(pids)
        if not term_ids:
            return set()
        result = term_ids if result is None else result & term_ids
    return result or set()


def _normalized_match_fields(product: dict) -> tuple[str, str, str]:
    """(navn, mærke, beskrivelse) normaliseret - memoized på selve dict'en.

    product_matches_query og product_matches_query_fuzzy kaldes begge på
    NØJAGTIG samme display-dict ved en søgning uden hits (streng søgning
    først, derefter den typo-tolerante fallback). normalize_name er regex-tung
    (NFKD + 20 forkortelses-substitutioner), så uden denne memo normaliseres
    hvert produkts tre felter to gange pr. søgning - altså netop når brugeren
    har skrevet forkert. Målt 2026-08-06: 151.657 normalize_name-kald på én
    søgning uden hits.

    Sikkert at gemme på dict'en: display-dicts bygges friskt pr. request af
    product_to_display_dict, og product_for_active_stores/
    _promote_match_to_product returnerer altid en kopi - der findes ingen
    delt, langtidslevende dict at forurene."""
    cached = product.get('_norm_fields')
    if cached is not None:
        return cached
    fields = (
        normalize_name(str(product.get('name', ''))),
        normalize_name(str(product.get('brand', ''))),
        normalize_name(str(product.get('description', ''))),
    )
    product['_norm_fields'] = fields
    return fields


def product_matches_query(product: dict, query: str) -> bool:
    """Token-baseret søgning (hele ord / præfiks / sammensætning).

    Both query and product fields go through normalize_name (not just
    .lower()) so a search for "hakket svinekød" also finds a card whose
    displayed title is Rema's abbreviated "HK. SVINEKØD" - normalize_name
    canonicalizes both spellings to the same "hakket" token.

    Matcher ikke midt i andre ord: "øl" rammer ikke "pølser"."""
    terms = normalize_name(query).split()
    if not terms:
        return False
    name, brand, desc = _normalized_match_fields(product)
    cheap_fields = (name, brand, desc)
    # Dovent: _product_flavor_search_field gør regex-tungt billed-URL-opslag
    # og skal ikke betales for produkter der allerede matcher på navn/mærke/
    # beskrivelse - langt de fleste. Kun ét opslag pr. produkt (memoized i
    # _flavor efter første kald), ikke pr. term.
    _flavor: list[str] = []

    def flavor() -> str:
        if not _flavor:
            _flavor.append(_product_flavor_search_field(product))
        return _flavor[0]

    def term_matches(term: str) -> bool:
        if any(_field_matches_term(f, term) for f in cheap_fields if f):
            return True
        # Smagsfeltet kan kun indeholde ord fra _FLAVOR_MAP, så et søgeord der
        # ikke rammer noget dér kan pr. definition ikke reddes af opslaget.
        # Uden denne linje betaler en søgning UDEN hits det regex-tunge
        # opslag for hvert eneste produkt - se _term_can_match_flavor.
        if not _term_can_match_flavor(term):
            return False
        fl = flavor()
        return bool(fl) and _field_matches_term(fl, term)

    return all(term_matches(term) for term in terms)


def _fuzzy_term_hits(term: str, words: list[str], threshold: float = 82.0) -> bool:
    """True hvis `term` fuzzy-matcher et enkelt ord i `words` (tolererer småfejl/tastefejl)."""
    if len(term) < 4:
        return False
    for w in words:
        if not w or abs(len(w) - len(term)) > 3:
            continue
        if max(rapid_ratio(term, w), rapid_token_sort(term, w)) >= threshold:
            return True
    return False


def product_matches_query_fuzzy(product: dict, query: str) -> bool:
    """Typo-tolerant fallback - bruges kun når streng token-søgning ikke giver hits
    (fx "minmælk" -> "minimælk"). Kaldes ikke pr. request, kun når resultatet ellers er tomt.
    Normaliseret som product_matches_query, af samme grund (abbreviation-symmetri)."""
    terms = normalize_name(query).split()
    if not terms:
        return False
    name, brand, desc = _normalized_match_fields(product)
    cheap_fields = (name, brand, desc)
    cheap_words = (name + ' ' + brand).split()
    # Dovent som product_matches_query - se dens kommentar. Fuzzy-fallback
    # kaldes kun ved 0 hits fra den strenge søgning, så den rammer typisk et
    # STØRRE antal produkter end den strenge - endnu vigtigere at spare
    # flavor-opslaget her når det kan undgås.
    _flavor: list[str] = []

    def flavor() -> str:
        if not _flavor:
            _flavor.append(_product_flavor_search_field(product))
        return _flavor[0]

    def term_matches(term: str) -> bool:
        if any(_field_matches_term(f, term) for f in cheap_fields if f):
            return True
        if _fuzzy_term_hits(term, cheap_words):
            return True
        # Samme genvej som i product_matches_query, men med fuzzy-varianten:
        # kan termen hverken ramme eller fuzzy-ramme ordforrådet, er opslaget
        # spildt for alle produkter. Det er netop denne fallback der ellers
        # gør en tastefejl dyr, fordi den kun kaldes NÅR intet matcher.
        if not _term_can_fuzzy_match_flavor(term):
            return False
        fl = flavor()
        if fl and (_field_matches_term(fl, term) or _fuzzy_term_hits(term, fl.split())):
            return True
        return False

    return all(term_matches(term) for term in terms)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

DEFAULT_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'da,da-DK;q=0.9,en;q=0.8',
}

_STORE_CONFIGS = {
    'rema':       {'db_key': None,           'label': 'Rema 1000',    'logo': '/static/images/Rema1000-logo.png'},
    'bilka':      {'db_key': 'Bilka',        'label': 'Bilka',        'logo': '/static/images/bilka-logo.png'},
    'netto':      {'db_key': 'Netto',        'label': 'Netto',        'logo': '/static/images/netto-logo.png'},
    'foetex':     {'db_key': 'Foetex',      'label': 'Føtex',        'logo': '/static/images/foetex-logo.png'},
    'mk':         {'db_key': 'minkøbmand',   'label': 'Min Købmand',  'logo': '/static/images/Min_kobmand_logo.png'},
    'meny':       {'db_key': 'Meny',         'label': 'Meny',         'logo': '/static/images/meny-logo.png'},
    'spar':       {'db_key': 'Spar',         'label': 'Spar',         'logo': '/static/images/spar-logo.png'},
    'sb':         {'db_key': 'SuperBrugsen', 'label': 'SuperBrugsen', 'logo': '/static/images/superbrugsen-logo.png'},
    'brugsen':    {'db_key': 'Brugsen',      'label': 'Brugsen',      'logo': '/static/images/brugsen-logo.png'},
    'kvickly':    {'db_key': 'Kvickly',      'label': 'Kvickly',      'logo': '/static/images/kvickly-logo.png'},
    'discount365':{'db_key': '365discount',  'label': '365 Discount', 'logo': '/static/images/365discount-logo.png'},
    'lidl':       {'db_key': 'Lidl',         'label': 'Lidl',         'logo': '/static/images/lidl-logo.png'},
    'loevbjerg':  {'db_key': 'Løvbjerg',     'label': 'Løvbjerg',     'logo': '/static/images/loevbjerg-logo.png'},
    'abclavpris': {'db_key': 'ABC Lavpris',  'label': 'ABC Lavpris',  'logo': '/static/images/abc-lavpris-logo.png'},
}

# Bump when a new butik tilføjes - klient og server auto-aktiverer nye butikker.
STORE_CATALOG_VERSION = 3
STORES_ADDED_IN_VERSION = {
    2: ['Lidl'],
    3: ['Løvbjerg', 'ABC Lavpris'],
}


def stores_auto_enable_since(saved_version: int) -> list[str]:
    labels: list[str] = []
    for ver in range(saved_version + 1, STORE_CATALOG_VERSION + 1):
        labels.extend(STORES_ADDED_IN_VERSION.get(ver, []))
    return labels


# ── Næringsindhold ────────────────────────────────────────────────────────────
# Data ligger i Supabase-tabellen nutrition_data (key -> payload), bygget offline
# af scripts/build-nutrition.py. Kort-til-kilde-mappingen udledes her ved opslag
# (ikke gemt), da kort-id'er skifter ved hver cache-genopbygning - kun EAN/Rema-id
# er stabile nøgler. Selve Supabase-kaldet sker i app.py (samme _supabase_rest-
# helper som prishistorik), så denne funktion er ren og uden I/O.
def _valid_ean(value) -> str | None:
    s = str(value or '').strip()
    return s if s.isdigit() and len(s) in (8, 12, 13, 14) else None


# Salling-solokort taber deres EAN i cache-opbygningen, men navnet stammer fra
# samme Algolia-kilde som build-nutrition.py dumper. Vi genforbinder derfor på
# normaliseret navn -> varens egen infos-næring. Nøglen hashes, så den er sikker
# i PostgREST's key=in.(...)-filter (ingen mellemrum/specialtegn).
_SALLING_LABEL_TO_KEY = {'Bilka': 'bilka', 'Netto': 'netto', 'Føtex': 'foetex'}


def sname_key(store_key: str, name) -> str | None:
    norm = normalize_name(name)
    if not norm:
        return None
    return f'sname:{store_key}:{hashlib.md5(norm.encode()).hexdigest()[:12]}'


def salling_sname_key(product: dict) -> str | None:
    store_key = _SALLING_LABEL_TO_KEY.get(product.get('/product/store') or '')
    if not store_key:
        return None
    return sname_key(store_key, product.get('/product/title') or product.get('/product/name'))


def nutrition_candidate_keys(product: dict) -> list[str]:
    """Prioriterede opslagsnøgler for et varekort - Rema-anker først, så EAN fra
    en hvilken som helst butik i den matchede gruppe (kortet dækkes af hele
    gruppens data, ikke kun dets egen visningsbutik), og til sidst en navne-nøgle
    der genforbinder Salling-solokort uden EAN til varens egen næring."""
    keys: list[str] = []
    try:
        if float(product.get('/product/rema_price') or 0) > 0:
            keys.append(f"rema:{product.get('/product/id')}")
    except (TypeError, ValueError):
        pass
    # Solokortets eget EAN (fra dets visningsbutik) + EAN fra hele den matchede gruppe.
    own_ean = _valid_ean(product.get('/product/ean'))
    for ean in ([own_ean] if own_ean else []) + [
            _valid_ean((m or {}).get('ean')) for m in (product.get('/product/store_matches') or {}).values()]:
        if ean:
            key = f'ean:{ean}'
            if key not in keys:
                keys.append(key)
    sname = salling_sname_key(product)
    if sname:
        keys.append(sname)
    return keys


def format_price(price_str):
    if not price_str:
        return 0.0
    try:
        cleaned = str(price_str).replace('DKK', '').replace('kr', '').replace(',', '.').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        logger.error(f"Error converting price: {price_str}")
        return 0.0


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

_ABBREV_COMPILED: list[tuple] = [
    (re.compile(r'\bsc\b'),      'sour cream'),
    (re.compile(r'\bhk\b'),      'hakket'),
    (re.compile(r'\bfuldk\b'),   'fuldkorn'),
    # (?<!f ) undgår at "F.eks."/"f eks" ("for eksempel") udvides til
    # "ekstra" - punktum er allerede blevet mellemrum på dette tidspunkt.
    (re.compile(r'(?<!f )\beks\b'), 'ekstra'),
    (re.compile(r'\bkyl\b'),     'kylling'),
    (re.compile(r'\bkart\b'),    'kartoffel'),
    (re.compile(r'\bchamp\b'),   'champignon'),
    (re.compile(r'\bsdj\b'),     'sønderjysk'),
    (re.compile(r'\bøko\b'),     'okologisk'),
    # Fjernet: \bo\b->'onion', \borg\b->'okologisk', \bmin\b->'mini',
    # \bsr\b->'sour' - alle fire bekræftet fejludvidet på rigtige varenavne
    # (OCR'ede nuller/V.S.O.P./D.O.C. -> "onion"; "Org."="Original" på
    # vin/sodavand-etiketter, ikke økologisk; "STR. MIN. 56" størrelses-
    # gradering -> "mini"; brandet "SR Food" -> "sour food"). Se
    # matchmotor-revisionen 2026-08-16, fund H5.
    # vanilla stavet på dansk/fr/en → fælles form
    (re.compile(r'\bvanille\b'), 'vanilje'),
    (re.compile(r'\bvanilla\b'), 'vanilje'),
    # normalisering af smørbar-varianter (inkl. bilka scrape fejl)
    (re.compile(r'\bsmørbart\b'), 'smørbar'),
    (re.compile(r'\bsmrbar\b'), 'smørbar'),
    # Smagsforkortelser fra dagligvare-feeds
    (re.compile(r'\bhyldebl\b'),  'hyldeblomst'),
    (re.compile(r'\bhindb\b'),    'hindbaer'),
    (re.compile(r'\bjordb\b'),    'jordbaer'),
    (re.compile(r'\bpeberm\b'),   'pebermynte'),
    (re.compile(r'\bchokol\b'),   'chokolade'),
    (re.compile(r'\bvanilj\b'),   'vanilje'),
]
_OKOLOGISK_RE = re.compile(r'\bokologisk\b')


@lru_cache(maxsize=16384)
def normalize_name(name):
    if not name or str(name) == 'nan':
        return ''
    name = str(name).lower().strip()
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    name = name.replace('&', 'and').replace('+', 'and').replace(',', ' ')
    # Punktum og apostrof skal væk FØR forkortelses-udvidelsen: "hk." blev
    # ellers til "hakket." og fejlede første-token-gaten i _find_generic_match
    # (substring-tjek), så fx Rema "HK. OKSEKØD 4-7%" aldrig kunne matche
    # Bilkas "Hakket oksekød 4-7% fedt". Punktum bliver ordskille (så
    # "fuldk.knækbrød" splittes), apostrof fjernes helt ("lay's" ↔ "lays").
    name = name.replace("'", '').replace('’', '')
    name = name.replace('.', ' ')
    for pattern, replacement in _ABBREV_COMPILED:
        name = pattern.sub(replacement, name)
    for noise in ['%', ' eko', ' bio', ' a/s', ' i/s']:
        name = name.replace(noise, '')
    name = _OKOLOGISK_RE.sub('', name)
    name = name.replace('/', ' ')
    return ' '.join(name.split())


def _compile_keyword_patterns(keyword_map) -> list:
    """(mønster, kanoniske navne)-liste, længste nøgleord først.

    Mønstret kræver ordgrænse i mindst én ende af forekomsten: rene
    substring-hits inde i et andet ord ('cola' i "chocolat") afvises, mens
    danske sammensætninger stadig fanges i begge ender ("jordbærsmag",
    "mælkechokolade"). Kanonisk navn kan være en tuple, når ét nøgleord skal
    give flere smage (fx vandmelon → watermelon + melon)."""
    patterns = []
    for kw, canonical in sorted(keyword_map, key=lambda x: -len(x[0])):
        esc = re.escape(kw)
        patterns.append((
            re.compile(rf'(?<![a-zæøå]){esc}|{esc}(?![a-zæøå])'),
            (canonical,) if isinstance(canonical, str) else tuple(canonical),
        ))
    return patterns


# Sammensætnings-suffikser skjuler smagen midt i ordet ("saltkaramelSMAG",
# "pebermynteFYLD", "mælkechokoladeOVERTRÆK") for ordgrænse-kravet - de
# strippes, så smagsordet ender ved ordgrænsen igen. Lookbehind sikrer, at
# fritstående ord ("smag", "fyld") ikke røres. Strip kan kun EKSPONERE
# smagsord, aldrig fjerne dem.
_SMAG_SUFFIX_RE = re.compile(r'(?<=[a-zæøå])(?:smag(?:s|en)?|fyld|overtræk|stang|stænger)\b')

# Kontekster hvor et smagsnøgleord IKKE er en smag: "druesukker" (glukose, ikke
# drue-smag), "colada" (piña colada indeholder 'cola'), brandet "Løgismose"
# (indeholder 'løg'), "tunge" (okse-/røget tunge, ikke 'tun') og fiskebrandet
# "Neptun" (ender på 'tun'). Fjernes fra teksten før nøgleords-scanning.
# "chocolat"/"chocolatier" (indeholder 'cola') klares af ordgrænse-kravet i
# _extract_keywords.
_FLAVOR_BLOCKERS_RE = re.compile(r'druesukker|colada|løgismose|tunge|neptun')

_FLAVOR_MAP = {
    # Sodavand / juice
    'cola': 'cola',
    'vindrue': 'grape', 'grape': 'grape',
    'hyldeblomst': 'elderflower', 'elderflower': 'elderflower',
    'mango': 'mango',
    'ananas': 'pineapple', 'pineapple': 'pineapple',
    'appelsin': 'orange', 'orange': 'orange',
    'citron': 'lemon', 'lemon': 'lemon',
    'lime': 'lime',
    # 'sour' og 'sour cream' deler kanonisk navn: "Kims Sour & Onion" er en
    # forkortelse af "sour cream & onion", så et skel ville afvise korrekte
    # chips-matches. Slik-siden ("Katjes Sour") rammes ikke - begge sider af
    # et korrekt slik-match nævner 'sour'.
    'sour': 'sour', 'sour cream': 'sour', 'sourcream': 'sour',
    'granatæble': 'pomegranate', 'pomegranate': 'pomegranate',
    'tranebær': 'cranberry', 'cranberry': 'cranberry',
    # Frugt / bær (yoghurt, skyr, marmelade osv.)
    'hindbær': 'raspberry', 'raspberry': 'raspberry',
    'jordbær': 'strawberry', 'strawberry': 'strawberry',
    'blåbær': 'blueberry', 'blueberry': 'blueberry',
    'solbær': 'blackcurrant', 'blackcurrant': 'blackcurrant',
    'stikkelsbær': 'gooseberry',
    'kirsebær': 'cherry', 'cherry': 'cherry',
    'pære': 'pear', 'pear': 'pear',
    'banan': 'banana', 'banana': 'banana',
    'æble': 'apple', 'apple': 'apple',
    'fersken': 'peach', 'peach': 'peach',
    'abrikos': 'apricot', 'apricot': 'apricot',
    'guava': 'guava',
    'passionsfrugt': 'passionfruit', 'passion': 'passionfruit',
    'kokos': 'coconut', 'coconut': 'coconut',
    'rabarber': 'rhubarb', 'rhubarb': 'rhubarb',
    'melon': 'melon',
    # Vandmelon giver BÅDE watermelon og melon: butikker forkorter til "Melon"
    # ("Extra Refresh Melon"), som ellers ville afvises asymmetrisk, mens
    # honning-/galiamelon stadig adskilles fra vandmelon på watermelon-smagen.
    'watermelon': ('watermelon', 'melon'), 'vandmelon': ('watermelon', 'melon'),
    'drue': 'grape',  # dækker også "druer" (ordstart); "vindruer" fanges af 'vindrue'
    'skovbær': 'forestberry',
    # Krydderurter/krydderier som varianter ("Tomatsuppe m. timian" ≠ "Tomatsuppe")
    'timian': 'thyme',
    'basilikum': 'basil',
    'oregano': 'oregano',
    'hvidløg': 'garlic',
    'h.løg': 'garlic',  # Dagrofa-forkortelse ("Flødeost H.Løg") - konsumeres før 'løg' (onion)
    'chili': 'chili',
    'karry': 'curry',
    # Smagsvarianter
    'naturel': 'natural', 'natural': 'natural', 'naturlig': 'natural',
    'vanilje': 'vanilla', 'vanilla': 'vanilla',
    'kakao': 'cocoa', 'cocoa': 'cocoa',
    'chokolade': 'chocolate', 'chocolate': 'chocolate',
    'honning': 'honey', 'honey': 'honey',
    'karamel': 'caramel', 'caramel': 'caramel',
    'karameller': 'caramel',  # flertalsform: 'karamel' står internt i "lakridskarameller" uden ordgrænse
    'mint': 'mint', 'mynte': 'mint',
    'spearmint': 'mint',  # ordgrænse-matcheren fanger ikke 'mint' inde i "spearmintsmag"
    'kaffe': 'coffee', 'coffee': 'coffee',
    'choko': 'chocolate',  # Rema-forkortelse ("choko" i titel/desc, ikke "chokolade")
    'choco': 'chocolate',  # engelsk forkortelse ("Cruesli Dark Choco", "Choco Treats")
    'chokol': 'chocolate',  # trunkeret feed-navn ("...m. mælkechokol")
    # Salte snack-smage (chips/tortilla, syltevarer osv.) - fanger fejlmatch som
    # "Røget torskelever" ↔ "Røget bacon" og "Syltede agurker" ↔ "Syltede rødløg".
    # Bemærk: 'salt' (også "m. salt"/"havsalt") og 'creme fraiche' er bevidst
    # udeladt. Salling beskriver mærkevarer generisk ("Chips m. salt" = Taffel/
    # Kettle/Danske Franske, hvis egne navne ikke nævner salt), og creme fraiche
    # er oftest selve MEJERIVAREN med afkortede navne ("CREME F.", "Fraiche 9%")
    # - begge ville afvise langt flere korrekte matches end de fanger fejl.
    'paprika': 'paprika',
    'bacon': 'bacon',
    'løg': 'onion', 'onion': 'onion',  # 'hvidløg' (garlic) konsumeres først, se _extract_keywords
    # Fisketyper: fisken ER produktnavnet ("Tun i tomat" ≠ "Makrel i tomat"),
    # så udeladelses-risikoen fra kød (frikadeller nævner ikke 'svin') findes
    # ikke her. Fanger fx tun↔makrel, ørred↔makrel og mørksej↔laks.
    'laks': 'laks', 'tun': 'tun', 'torsk': 'torsk', 'makrel': 'makrel',
    'sild': 'sild', 'ørred': 'ørred', 'rødspætte': 'rødspætte',
    'reje': 'reje', 'rejer': 'reje',
    'musling': 'musling', 'blåmusling': 'musling',
    'mørksej': 'sej', 'sejfilet': 'sej',  # bart 'sej' er for kollisionsudsat
}


def _extract_keywords(text_lower: str, patterns: list) -> set:
    """Scan med længste nøgleord først og konsumér hver forekomst, så et kortere
    nøgleord ikke gen-matcher inde i et længere ("sour cream" skal ikke også
    give 'sour'; "hvidløg" (garlic) skal ikke også give 'løg' (onion)).

    Kører til fixpoint: en konsumering kan eksponere en ordgrænse for et
    nøgleord, der allerede var afprøvet ("chokokaramel" → 'choko' konsumeres
    og frigør 'karamel', som er længere og derfor blev scannet først)."""
    found = set()
    changed = True
    while changed:
        changed = False
        for pattern, canonicals in patterns:
            m = pattern.search(text_lower)
            if m:
                found.update(canonicals)
                text_lower = f"{text_lower[:m.start()]} {text_lower[m.end():]}"
                changed = True
    return found


# Kompileret én gang ved opstart - get_product_flavors kaldes i matchingens
# inderloops, så sortering/kompilering må ikke ske pr. kald.
_FLAVOR_PATTERNS = _compile_keyword_patterns(_FLAVOR_MAP.items())


def get_product_flavors(text: str) -> set:
    """Udtræk kanoniske smagsnavne fra produkttekst (længeste nøgleord først)."""
    cleaned = _SMAG_SUFFIX_RE.sub(' ', _FLAVOR_BLOCKERS_RE.sub(' ', text.lower()))
    return _extract_keywords(cleaned, _FLAVOR_PATTERNS)


def extract_image_flavor_keywords(image_url: str) -> set:
    """Udtræk smagsord fra produktbillede URL/filnavn (normalize_name udvider fx hyldebl)."""
    if not image_url or str(image_url).lower() in ('nan', 'none', ''):
        return set()
    url_clean = normalize_name(
        image_url.lower().replace('-', ' ').replace('_', ' ').replace('/', ' ')
    )
    return get_product_flavors(url_clean)


def get_search_flavor_keywords(text: str, image_url: str = '') -> str:
    """Alle danske og kanoniske smags-søgeord til berigelse af søgeindeks/search_text."""
    canonicals = get_product_flavors(text)
    if image_url:
        canonicals.update(extract_image_flavor_keywords(image_url))
    if not canonicals:
        return ''
    reverse_map: dict[str, set[str]] = {}
    for kw, canon in _FLAVOR_MAP.items():
        c_list = [canon] if isinstance(canon, str) else list(canon)
        for c in c_list:
            reverse_map.setdefault(c, set()).add(kw)
    result_words = set()
    for c in canonicals:
        result_words.add(c)
        result_words.update(reverse_map.get(c, set()))
    return ' '.join(result_words)


def _build_flavor_vocabulary() -> tuple[str, ...]:
    """Alle tokens et smagsfelt overhovedet KAN indeholde.

    get_search_flavor_keywords() samler udelukkende ord fra _FLAVOR_MAP -
    kanoniske værdier plus de nøgleord der peger på dem. Intet fra
    produktteksten eller billed-URL'en slipper igennem ordret; de bruges kun
    til at slå op i kortet. Smagsfeltet for ethvert produkt er derfor en
    delmængde af dette faste ordforråd (105 tokens), og det gør det muligt at
    afgøre ÉN gang pr. søgeord om et smags-opslag overhovedet kan give hit -
    se _term_can_match_flavor."""
    words = set(_FLAVOR_MAP)
    for canon in _FLAVOR_MAP.values():
        if isinstance(canon, str):
            words.add(canon)
        else:
            words.update(canon)
    tokens: set[str] = set()
    for word in words:
        tokens.update(normalize_name(word).split())
    # Også normaliseret samlet: _product_flavor_search_field kører
    # normalize_name på den SAMMENSATTE streng, og en regel der virker hen
    # over en ordgrænse ville ellers kunne give et token vi ikke kender.
    tokens.update(normalize_name(' '.join(sorted(words))).split())
    return tuple(sorted(t for t in tokens if t))


_FLAVOR_VOCAB = _build_flavor_vocabulary()


@lru_cache(maxsize=1024)
def _term_can_match_flavor(term: str) -> bool:
    """Kan `term` overhovedet ramme ET produkts smagsfelt (streng matchning)?

    Falsk her betyder at _field_matches_term(flavor, term) er falsk for
    ALLE produkter - så det regex-tunge smags-opslag kan springes helt over
    i stedet for at blive betalt pr. produkt. Det er præcis det der gør en
    tastefejl dyr: ved en søgning uden hits falder hvert eneste produkt
    igennem navn/mærke/beskrivelse og ned i smags-opslaget. Målt 2026-08-06:
    'xyzqwe' gav 37.912 kald til get_search_flavor_keywords og 8,9 mio.
    regex-søgninger (26,5 s) - med denne genvej bliver det nul.

    Memoized pr. term, så prisen er 105 sammenligninger pr. UNIKT søgeord,
    ikke pr. produkt."""
    return any(_token_matches_term(vocab, term) for vocab in _FLAVOR_VOCAB)


@lru_cache(maxsize=1024)
def _term_can_fuzzy_match_flavor(term: str) -> bool:
    """Som _term_can_match_flavor, men for den typo-tolerante fallback, der
    også accepterer et fuzzy hit på et enkelt ord i smagsfeltet."""
    if _term_can_match_flavor(term):
        return True
    return _fuzzy_term_hits(term, list(_FLAVOR_VOCAB))


@lru_cache(maxsize=32768)
def _cached_search_flavor_field(raw_text: str, img: str) -> str:
    # maxsize skal komfortabelt overstige kataloget (~19.779 produkter,
    # 2026-08-17). Var tidligere 8192 - mindre end kataloget, så en enkelt
    # fuld scanning (korte/ugyldige søgeord uden indeks-hit, se
    # _filter_products_for_search) skyllede tidlige poster ud før scanningen
    # var færdig, og selv identiske gentagne requests fik ingen cache-gevinst
    # (målt 7-20 s pr. kald, ingen speedup ved gentagelse - QA-audit 2026-08-17).
    kw = get_search_flavor_keywords(raw_text, img)
    return normalize_name(kw) if kw else ''


def _product_flavor_search_field(product: dict) -> str:
    """Normaliseret smagsfelt til product_matches_query (inkl. billed-URL).

    Memoized: product_matches_query og product_matches_query_fuzzy kaldes
    begge pr. produkt pr. søgning (streng søgning, så typo-tolerant fallback
    ved 0 hits) - uden cache regnes samme produkts regex-tunge
    billed-URL-parsing (get_search_flavor_keywords) dermed dobbelt for hvert
    produkt i kandidatpuljen (op til 800) på HVER søgning. Målt i produktion
    2026-08-05: det alene var nok til at overskride Workers' CPU-budget
    (introspection.CpuLimitExceeded) under samtidige søgninger uden direkte
    match. Nøglen er selve teksten/billed-URL'en (ikke produkt-id'et), så
    cachen rammer på tværs af requests for uændrede produkter.

    Bruger et præcomputeret felt fra nattens seed (scripts/seed-d1.py) hvis
    det findes - så er der INGEN regex-tung beregning tilbage overhovedet,
    heller ikke på en helt frisk isolate uden noget i lru_cache endnu. Tom
    streng (fx ældre cache fra før denne ændring, eller produktet reelt uden
    smagsord) falder blødt tilbage til live-beregningen nedenfor."""
    # Nøgle-tjek, ikke sandhedsværdi: et præberegnet smagsfelt er TOMT for 74%
    # af kataloget (de fleste varer har ingen smagsord overhovedet). Med et
    # `if precomputed:` faldt netop de produkter tilbage til live-beregningen
    # ved hver eneste søgning, også efter nattens seed - så præberegningen kun
    # virkede for den fjerdedel der havde smagsord. Målt 2026-08-06 på 800
    # kandidater: 595 af dem regnede feltet ud igen og stod for 0,41 s af
    # requestens 0,50 s. product_to_display_dict sætter kun nøglen når det rå
    # produkt faktisk bar '/product/flavor_kw', så et manglende felt (cache
    # fra før seed'et) stadig falder korrekt tilbage til beregningen nedenfor.
    if '_flavor_field' in product:
        return product['_flavor_field'] or ''
    raw_text = ' '.join([
        str(product.get('name') or product.get('/product/title', '')),
        str(product.get('brand') or product.get('/product/brand', '')),
        str(product.get('description') or product.get('/product/description', '')),
    ])
    img = str(product.get('image_url') or product.get('/product/imageLink', '')).strip()
    return _cached_search_flavor_field(raw_text, img)


def fuzzy_score(a, b):
    if not a or not b: return 0.0
    if a == b: return 1.0
    la, lb = len(a), len(b)
    if (2.0 * min(la, lb) / (la + lb)) < 0.35:
        # Undtagelse: hvis det korteste navn er et helt ord inde i det
        # længste (fx Rema-terse "æg" mod "økologiske æg fra frilandshøns,
        # 10 stk"), skal den billige længde-kortslutning ikke forhindre en
        # reel score - lad selve scoreren (og downstream-gates) afgøre det
        # i stedet for et hårdt 0.0 uanset indholdsoverlap. Se matchmotor-
        # revisionen 2026-08-16, fund H6.
        shorter, longer = (a, b) if la <= lb else (b, a)
        if not re.search(r'\b' + re.escape(shorter) + r'\b', longer):
            return 0.0
    # max af ratio (følsom for ordstilling) og token_sort (ufølsom for ordstilling),
    # så fx "Rød peberfrugt" ≈ "Peberfrugt rød" matcher. token_set bruges bevidst IKKE,
    # da den over-matcher delmængder (fx "Kaffe" ≈ "Kaffe Filter").
    return max(rapid_ratio(a, b), rapid_token_sort(a, b)) / 100.0


# ---------------------------------------------------------------------------
# Perceptual image hash (pHash) – bruges til Rema ↔ butik fuzzy matching
# ---------------------------------------------------------------------------

_HASH_CANDIDATE_MAX_DIST = 12  # Hamming distance; matcher eksisterende gate-lempelse i updater


def phash_hex_to_int(hex_str: str) -> int | None:
    """Konverter pHash-hex (fra imagehash eller Supabase) til int."""
    s = str(hex_str or '').strip()
    if not s or s.lower() in ('nan', 'none'):
        return None
    try:
        return int(s, 16)
    except ValueError:
        return None


def hash_hamming_distance(hash_a: int, hash_b: int) -> int:
    return (hash_a ^ hash_b).bit_count()


def hash_candidate_indices(r_hash_int: int, hash_list: list, max_dist: int = _HASH_CANDIDATE_MAX_DIST) -> set[int]:
    """Find produkt-indeks med pHash inden for max_dist (til kandidatsøgning)."""
    if r_hash_int is None or not hash_list:
        return set()
    return {
        i for i, p_hash_int in hash_list
        if hash_hamming_distance(r_hash_int, p_hash_int) <= max_dist
    }


def compute_image_hash(url: str, timeout: int = 5) -> str:
    """Hent produktbillede og beregn perceptual hash (hex-streng)."""
    if not url or str(url).strip().lower() in ('nan', 'none', ''):
        return ''
    try:
        import requests
        from io import BytesIO
        from PIL import Image
        import imagehash

        response = requests.get(url, timeout=timeout, headers=DEFAULT_HTTP_HEADERS)
        response.raise_for_status()
        return str(imagehash.phash(Image.open(BytesIO(response.content))))
    except Exception:
        return ''


def attach_billede_hashes(rows: list[dict], workers: int = 8) -> None:
    """Beregn billede_hash in-place for rækker med billede_url."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = [
        (i, r['billede_url'])
        for i, r in enumerate(rows)
        if r.get('billede_url') and not r.get('billede_hash')
    ]
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(compute_image_hash, url): i for i, url in jobs}
        for future in as_completed(futures):
            idx = futures[future]
            h = future.result()
            if h:
                rows[idx]['billede_hash'] = h


# ---------------------------------------------------------------------------
# Weight / unit parsing
# ---------------------------------------------------------------------------

# Vægt-gate: relativ tolerance med en lille absolut bund, så 100 g vs. 150 g
# ikke matcher (8% = 8 g), mens 1000 g vs. 1050 g stadig gør (8% = 80 g).
# For småvarer (krydderier, pastiller, tyggegummi) skaleres den absolutte bund
# ned til 25% af vægten - ellers lod de faste 20 g fx en 20 g-pastilæske
# matche en 40 g-æske (dobbelt størrelse).
_WEIGHT_TOLERANCE_G = 20      # grams / ml - minimum (afrundinger, 500 g vs 510 g)
_WEIGHT_TOLERANCE_REL = 0.08  # 8% af den største af de to vægte

_WEIGHT_RE = re.compile(r'^([\d.]+)\s*([a-zæøå]+)$')
# Salling-multipakker: "6 x 0.33 liter", "8 x 40.75 g" - totalvægt = antal × enhed.
# Uden denne parses de til None, og vægt-gaten bliver permissiv, så fx en Rema
# 0.33 l enkeltdåse kunne matche en 6-pak i Bilka/Netto/Føtex.
_MULTIPACK_RE = re.compile(r'^(\d+)\s*x\s*([\d.]+)\s*([a-zæøå]+)\.?$')
_STK_RE = re.compile(r'^([\d.]+)\s*st[k]?$')


def _unit_to_grams(value: float, unit: str) -> float | None:
    if unit in ('g', 'gr', 'gram'):     return value
    if unit in ('kg',):                  return value * 1000
    if unit in ('l', 'ltr', 'liter', 'litre'): return value * 1000
    if unit in ('ml',):                  return value
    if unit in ('cl',):                  return value * 10
    if unit in ('dl',):                  return value * 100
    return None


def parse_weight_to_grams(weight_str) -> float | None:
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    m = _MULTIPACK_RE.match(s)
    if m:
        try:
            count = int(m.group(1))
            value = float(m.group(2))
        except ValueError:
            return None
        unit_g = _unit_to_grams(value, m.group(3))
        return count * unit_g if unit_g is not None and count > 0 else None
    m = _WEIGHT_RE.match(s)
    if not m:
        return None
    try:
        value = float(m.group(1))
        unit = m.group(2)
    except ValueError:
        return None
    return _unit_to_grams(value, unit)


def parse_stk_count(weight_str) -> int | None:
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    m = _STK_RE.match(s)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def weights_compatible(w_a: float | None, w_b: float | None, tolerance: float | None = None) -> bool:
    if w_a is None or w_b is None:
        return True
    if tolerance is None:
        w_max = max(w_a, w_b)
        tolerance = max(min(_WEIGHT_TOLERANCE_G, 0.25 * w_max),
                        _WEIGHT_TOLERANCE_REL * w_max)
    return abs(w_a - w_b) <= tolerance


# ---------------------------------------------------------------------------
# Variant-heuristikker (deles af app.py-filtre og updater.py-matching)
# ---------------------------------------------------------------------------

# Ordgrænset stub ("økolog\w*" fanger økologisk/økologiske/økologi uden at
# kræve præcis bøjning). Den gamle `text.startswith('øko')`/`' øko'`-tjek
# fangede "Økonomipakke" som økologisk (øko- er fælles præfiks men "økolog"
# er det ikke), og den gamle 'økolog' in text-substring ignorerede negation
# ("Mælk ikke økologisk" blev også flagget økologisk). Se matchmotor-
# revisionen 2026-08-16, fund H1.
_ORGANIC_RE = re.compile(r'\bøkolog\w*|\bøko\b|\borganic\b')
_ORGANIC_NEGATED_RE = re.compile(r'\bikke\s+øko')


def is_organic(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as organic."""
    text = f"{name} {desc} {brand}".lower()
    if _ORGANIC_NEGATED_RE.search(text):
        return False
    return bool(_ORGANIC_RE.search(text))


def is_lactose_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as lactose-free."""
    text = f"{name} {desc} {brand}".lower()
    if 'laktosefri' in text or 'lactose free' in text or 'lactose-free' in text or 'laktose fri' in text:
        return True
    if 'lactofri' in text or 'lacto-free' in text or 'lactofree' in text:
        return True
    # Arla m.fl. bruger "Lacto" som produktlinje (ikke det samme som dansk "laktose" med k)
    if re.search(r'\blacto\b', text):
        return True
    return False


# Ordgrænset ('\bzero\b' i stedet for den gamle ' zero' substring) så
# "zero" ikke kan fyre midt inde i et andet ord. Se fund H1.
_SUGAR_FREE_RE = re.compile(
    r'sukkerfri|sugar[- ]free|sukker fri|zero sugar|\bzero\b|no sugar'
    r'|uden sukker|\bnul suk|0% sugar')


def is_sugar_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as sugar-free."""
    text = f"{name} {desc} {brand}".lower()
    # Harboe/Dagrofa-former: "Nul Sukker", forkortet "Nul Suk." og "0% Sugar"
    return bool(_SUGAR_FREE_RE.search(text))


_GLUTEN_FREE_RE = re.compile(
    r'glutenfri|gluten[- ]free|gluten fri|uden gluten')


def is_gluten_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as gluten-free."""
    text = f"{name} {desc} {brand}".lower()
    return bool(_GLUTEN_FREE_RE.search(text))


# "0,0%" står i praksis kun på alkoholfri drikkevarer (tjekket mod hele
# produkt-cachen) - fedtprocenter skrives 0,1%/0,5% og ikke 0,0%.
_ALCOHOL_FREE_RE = re.compile(
    r'alkoholfri|alkohol fri|alcohol[- ]free|\bnul ?%|\b0[,.]0 ?%')


def is_alcohol_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as alcohol-free."""
    return bool(_ALCOHOL_FREE_RE.search(f"{name} {desc} {brand}".lower()))


# Kødtype-gate: hakket/forarbejdet kød deler næsten hele navnet på tværs af
# butikker ("Hakket <kød> 8-12% fedt"), så navnescore + procent + vægt kan
# ikke skelne oksekød fra grise- eller kyllingekød. Teksten normaliseres før
# opslag, så forkortelser som "kyl." fanges som kylling. 'and' (fugl) udelades
# bevidst: normalize_name gør '&' til 'and', så ordet kan ikke skelnes fra
# sammenbinding. 'lammefjord' undtages (kartofler/gulerødder, ikke lam).
# Ligger her (ikke i updater.py) fordi erstatningsvare-søgningen i app.py
# bruger samme gate på edge, hvor updater ikke er importérbar.
_MEAT_PATTERNS: list[tuple] = [
    ('okse',    re.compile(r'\bokse')),
    ('gris',    re.compile(r'\bgris\b|\bgrise|\bsvin\b|\bsvine')),
    ('kylling', re.compile(r'\bkylling')),
    ('høns',    re.compile(r'\bhøns')),
    ('kalv',    re.compile(r'\bkalv\b|\bkalve')),
    ('lam',     re.compile(r'\blam\b|\blamme(?!fjord)')),
    ('skinke',  re.compile(r'\bskinke')),
    ('kalkun',  re.compile(r'\bkalkun')),
    ('tun',     re.compile(r'\btun\b|\btunfisk')),
    ('laks',    re.compile(r'\blaks')),
]


def get_meat_types(text: str) -> frozenset:
    """Kanoniske kødtyper nævnt i produktteksten (efter navne-normalisering)."""
    norm = normalize_name(text)
    return frozenset(name for name, rx in _MEAT_PATTERNS if rx.search(norm))


def meats_match(base_meats: frozenset, cand_meats: frozenset) -> bool:
    """Symmetrisk som procent-gaten: kun aktiv når BEGGE sider nævner kødtyper.

    En side uden kødord ("FRIKADELLER") er ikke en modsigelse, men nævner
    begge sider kød, skal sættet være identisk - "HK. OKSEKØD" må hverken
    matche "Hakket kyllingekød" eller blandingsproduktet "Hakket okse- og
    kyllingekød"."""
    return not base_meats or not cand_meats or base_meats == cand_meats


# ---------------------------------------------------------------------------
# Product filtering constants
# ---------------------------------------------------------------------------

_BLOCKED_NAME_FRAGMENTS = {
    # Personlig pleje
    # Bemærk: bare 'creme' undgås bevidst - rammer fødevarer som
    # "cremefraiche"/"flødecreme". Kun specifikke kosmetik-cremer blokeres.
    'indlæg', 'batteri', 'shampoo', 'balsam', 'lotion', 'bleer', 'ble',
    'blebukser', 'blebukse', 'pampers', 'libero', 'huggies', 'babylove',
    'skifteunderlag', 'vådliggerlagner', 'vådligger',
    'ansigtscreme', 'håndcreme', 'fodcreme', 'bodycreme', 'natcreme',
    'dagcreme', 'øjencreme', 'hudcreme', 'fugtighedscreme', 'børnecreme',
    'zinkcreme', 'hælecreme', 'babycreme', 'babybad', 'babyvask',
    'bleposer', 'vaskeserviet', 'vådserviet', 'skumvaskeklud', 'sutteflaske',
    'tandpasta', 'tandbørste', 'håndsæbe', 'shower gel', 'deodorant',
    'deospray', 'bind', 'tampon', 'hudpleje', 'parfume', 'solcreme',
    'sollotion', 'mascara', 'neglelak', 'makeupfjerner', 'brusegel',
    # Kæledyr
    'hundemad', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus',
    'pedigree', 'whiskas', 'felix', 'royal canin', 'purina', 'dreamies',
    # Rengøring & husholdning
    'opvaskemiddel', 'vaskemiddel', 'skyllemiddel', 'opvasketabs',
    'vaskekapsler', 'toiletrengøring', 'bref', 'domestos', 'harpic',
    'toiletpapir', 'toilet', 'køkkenrulle', 'køkken rulle',
    # Medicin & apotek (ikke fødevarer)
    'smertestillende', 'febernedsættende', 'medicin', 'apotek',
    'panodil', 'ipren', 'pamol', 'ibumetin', 'kodimagnyl', 'imodium',
    'alminox', 'pinex', 'magnyl', 'treo', 'aspirin', 'paracetamol',
    'ibuprofen', 'hostesaft', 'næsespray', 'øjenråber', 'øjendråber',
    'plaster', 'forbinding', 'kompres', 'sårpleje',
    'tabletter mod', 'tabletter børn',
    # Tobak / nikotin - også tjekket via is_age_restricted (titel+brand)
    'tobak', 'cigaret', 'cigaretter', 'cigarillo', 'cigar', 'snus', 'nikotin',
    'tændstik', 'lighter', 'fyrstikker', 'marlboro', 'winston', 'camel',
    'skjold rød', 'skjold blå', 'skjold grå', "king's", 'prince filter', 'prince røg',
    # Cigaretnavne uden ordet "cigaret" - de slap gennem filteret og blev
    # fejlmatchet på billed-hash (sundhedsadvarsler gør alle pakker ens for pHash)
    'hardbox', 'softbox', 'softpack', 'pall mall', 'l&m', 'lucky strike',
    'house of prince', 'chesterfield', 'gauloises', 'virg blend',
    'virginia blend', 'original blend no', 'bellman',
    'manitou', 'tigerbrand', 'escort gul', 'escort blå',
    'blød pakke', 'cecil original', 'prince rød', 'prince grå', 'prince blå',
    'viking rød', 'viking blå', 'viking grå',
    # Blade & magasiner
    'hjemmet', 'søndag', 'hendes verden', 'her og nu', 'billed bladet',
    'billedbladet', 'se og hør', 'ude og hjemme', 'ude & hjemme',
    '7-tv-dage', '7 tv dage', '7 tv-dage', 'alt for damerne', 'anders and',
    'zapp elektron', 'piberensere', 'ekstra bladet', 'ugeblad', 'magasin',
    # Planter & blomster
    'plante', 'planter', 'potteplante', 'potteplanter', 'potte', 'potteskjuler',
    'blomst', 'blomster',
    'buket', 'roser', 'tulipaner', 'orkidé', 'krysantemum', 'gødning',
    'pottejord', 'plantejord', 'havejord', 'blomsterjord', 'pottemuld', 'spagnum',
    # Maling & byggemarked
    'maling', 'maler', 'malersæt', 'pensel', 'penselsæt', 'spartel', 'spartelmasse',
    'tapet', 'fugemasse', 'silikone',
    # Tøj & tekstil
    'sneakers', 't-shirt', 'solbriller', 'badeklæde', 'leggings',
    'sengetøj', 'sengetæppe', 'pude', 'dyne', 'slipper', 'hjemmesko', 'kasket',
    # Møbler & have
    'havestol', 'spisebordsstol', 'lænestol', 'liggestol', 'klapstol',
    'gyngestol', 'havebord', 'sofabord', 'spisebord', 'havemøbel', 'havemøbler',
    'krukke', 'parasol', 'trolley',
    # Sæson & fritid
    'telt', 'nissehave', 'kridt', 'uneflex',
    # Maskiner & køkkengrej
    'kaffemaskine', 'espressomaskine', 'elkedel', 'airfryer', 'stegepande',
    'støvsuger', 'støvsugerpose',
    # Lys
    'stearinlys', 'fyrfadslys', 'kronelys', 'bloklys',
    # Kosttilskud
    'vitaminer', 'kosttilskud', 'proteinpulver', 'whey protein',
}

# Krav: kun mad - ingen undtagelser. Ekstra ikke-mad-termer ud over dem ovenfor.
# Bemærk: 'creme' er bevidst IKKE med (rammer fødevarer som "cremefraiche"/"is creme").
_EXTRA_NON_FOOD_TERMS = {
    # Kæledyr
    'hundefoder', 'kæledyrsfoder', 'dyrefoder', 'dyremad', 'kattesand',
    'kattebakke', 'hundelegetøj', 'kattemøbel', 'friskies', 'iams', 'sheba',
    # Rengøring & husholdning
    'sæbe', 'rengøringsmiddel', 'afkalker', 'afspændingsmiddel', 'wc-rens',
    'toiletrens', 'pletfjerner', 'tøjvask', 'skuresvamp', 'karklud', 'karklude',
    'viskestykke', 'affaldsposer', 'skraldeposer', 'fryseposer', 'husholdningsfilm',
    'alufolie', 'bagepapir', 'servietter', 'lommetørklæder', 'tørrestativ',
    # Personlig pleje
    'bodylotion', 'barberskum', 'barberblade', 'vatpinde', 'vatrondeller',
    'tandtråd', 'mundskyl', 'intimsæbe', 'sololie', 'solspray', 'solstift',
    'sæbespåner', 'deo',
    # Tøj, sko & tekstil
    'sokker', 'undertøj', 'strømper', 'badehåndklæde', 'håndklæde', 'viskestykker',
    # Elektronik, husgeråd, legetøj m.m.
    'lyspære', 'glødepære', 'batterier', 'opladelige', 'legetøj', 'spil',
    'puslespil', 'engangsservice', 'plastikkrus', 'paptallerken',
    # Forbrugerelektronik (fx Føtex sælger tv, telefoner og tilbehør).
    # Bemærk: bare 'tv' undgås bevidst - kolliderer med snacks som "TV-Mix".
    'smart tv', 'fjernsyn', 'oled', 'qled',
    'soundbar', 'høretelefon', 'høretelefoner', 'hovedtelefoner',
    'øretelefoner', 'earbuds', 'mobiltelefon', 'smartphone', 'telefon',
    'bærbar', 'laptop', 'oplader', 'powerbank', 'router', 'printer',
    'playstation', 'xbox', 'nintendo', 'smartwatch', 'højttaler',
    'kamera', 'overvågningskamera', 'videokamera', 'webcam',
    # Elektronik-mærker uden fødevarer (entydige i dagligvarekontekst)
    'samsung', 'iphone', 'ipad', 'ipod', 'macbook', 'airpods',
    'huawei', 'xiaomi', 'oneplus', 'hisense', 'prosonic', 'tp-link',
    'tcl', 'zte', 'doro', 'lg',
    # Kosttilskud & helse
    'fiskeolie', 'magnesium', 'd-vitamin', 'c-vitamin', 'multivitamin',
    'vitamintilskud', 'kreatin', 'collagen',
    # Medicin (supplerer _BLOCKED_NAME_FRAGMENTS)
    'smertestillende', 'febernedsættende', 'panodil', 'ipren', 'pamol',
    'ibumetin', 'imodium', 'paracetamol', 'ibuprofen',
}

# Ordgrænse-baseret regex: matcher kun hele ord, så fødevare-sammensætninger
# (fx "jordbær", "cremefraiche", "balsamico") ikke rammes ved et uheld.
#
# Grænse på BEGGE sider som standard. Et forsøg på at fjerne venstre-
# grænsen for hele listen på én gang (for at fange danske sammensætninger
# som "spraymaling"/"babyshampoo", jf. scraper/keywords.py's
# NON_FOOD_KEYWORDS) blev afprøvet mod hele produkt-cachen og forkastet:
# det gav 234 falske hits, domineret af 'ble' inde i "æble" (210 stk. -
# "ÆBLE JUICE", "Grødsmoothie m. æble..." osv.), samt 'blomst' i
# "hyldeblomst" (en almindelig sodavandssmag), 'lg'/'doro' (brands LG/Doro)
# inde i "valg"/"salg"/"pomodoro". Kun de konkret bekræftede, sikre
# sammensætnings-tilfælde lempes derfor enkeltvis i
# _NON_FOOD_SUFFIX_TERMS nedenfor - se matchmotor-revisionen 2026-08-16,
# fund H2 (og den efterfølgende rettelse af selve H2-rettelsen).
_NON_FOOD_NAME_TERMS = _BLOCKED_NAME_FRAGMENTS | _EXTRA_NON_FOOD_TERMS

# Kun disse to termer lempes til ordgrænse KUN til højre (dvs. tillader et
# vilkårligt præfiks, ligesom "maling" i "spraymaling"): bekræftet reelt
# non-food i cachen ("Spraymaling", "Møbelmaling") uden nogen fundet
# kollision med et fødevarenavn. "shampoo" tilføjet af samme grund
# ("Babyshampoo", "Hundeshampoo") - ingen dansk/engelsk fødevare ender på
# disse bogstavsekvenser.
_NON_FOOD_SUFFIX_TERMS = {'maling', 'shampoo'}
_NON_FOOD_BOTH_ANCHOR_TERMS = _NON_FOOD_NAME_TERMS - _NON_FOOD_SUFFIX_TERMS

_NON_FOOD_NAME_RE = re.compile(
    r'(?<![0-9a-zæøåäöü])(?:'
    + '|'.join(re.escape(t) for t in sorted(_NON_FOOD_BOTH_ANCHOR_TERMS, key=len, reverse=True))
    + r')(?![0-9a-zæøåäöü])'
    + r'|(?:'
    + '|'.join(re.escape(t) for t in sorted(_NON_FOOD_SUFFIX_TERMS, key=len, reverse=True))
    + r')(?![0-9a-zæøåäöü])',
    re.IGNORECASE,
)


def is_non_food_name(name: str) -> bool:
    """True hvis produktnavnet klart er en ikke-mad-vare (ordgrænse-match)."""
    return bool(name) and _NON_FOOD_NAME_RE.search(str(name).lower()) is not None


# ---------------------------------------------------------------------------
# Tobak / nikotin - må hverken vises eller matches (alkohol er OK)
# ---------------------------------------------------------------------------

# Rema-produkt-ID-intervaller for tobak (bruges også i app.py billedfilter)
_REMA_TOBACCO_ID_RANGES = ((521340, 521825), (561828, 561875))

# Tobak/nikotin i titel ELLER brand (Prince-cigaretter har brand HARDBOX)
_TOBACCO_RE = re.compile(
    r'(?<![0-9a-zæøå])(?:'
    r'tobak|cigaretter|cigaret|cigarillo|cigar|snus|nikotin|e-cigaret|e-cig|'
    r'marlboro|winston|camel|pall mall|lucky strike|chesterfield|gauloises|'
    r'hardbox|softbox|softpack|blød pakke|'
    r'house of prince|virg blend|virginia blend|original blend no|'
    r'bellman|manitou|tigerbrand|escort gul|escort blå|'
    r'prince filter|prince røg|prince rød|prince grå|prince blå|'
    r'prince original 100|viking rød|viking blå|viking grå|'
    r'skjold rød|skjold blå|skjold grå|cecil original|'
    r"king's|l&m"
    r')(?![0-9a-zæøå])',
    re.IGNORECASE,
)

# LU Prince-kiks må ikke rammes af tobaksfilteret
_LU_PRINCE_COOKIE_RE = re.compile(
    r'\blu\b.*prince|prince.*(?:kiks|cookie)|prince original 2-pak',
    re.IGNORECASE,
)


def is_rema_tobacco_id(product_id) -> bool:
    """True hvis Rema-produkt-ID ligger i tobaks-intervallerne."""
    try:
        pid = int(str(product_id).strip())
    except (TypeError, ValueError):
        return False
    return any(lo <= pid <= hi for lo, hi in _REMA_TOBACCO_ID_RANGES)


def is_age_restricted(
    name: str = '',
    brand: str = '',
    category: str = '',
    product_id: str = '',
) -> bool:
    """True for tobak/nikotin (må ikke vises eller matches).

    Alkohol er bevidst IKKE inkluderet - det er fødevarer/drikkevarer på sitet.
    Andre ikke-madvarer (bleer, shampoo, …) håndteres af is_non_food_name.
    """
    if product_id and is_rema_tobacco_id(product_id):
        return True

    blob = f'{name or ""} {brand or ""}'.strip().lower()
    if not blob:
        return False

    if _TOBACCO_RE.search(blob):
        if _LU_PRINCE_COOKIE_RE.search(blob):
            return False
        return True
    return False


_PLACEHOLDER_IMGS = {
    '/static/images/bilka-logo.png',
    '/static/images/Min_kobmand_logo.png',
    '/static/images/meny-logo.png',
    '/static/images/spar-logo.png',
    '/static/images/Rema1000-logo.png',
    'https://rema-product-images.digital.rema1000.dk/521365/1-large-bJ9YdpX0qL.webp',
    'https://rema-product-images.digital.rema1000.dk/521363/1-large-rDq68WajPb.webp',
    'https://rema-product-images.digital.rema1000.dk/521374/1-large-869DBK5MoM.webp',
}

# ---------------------------------------------------------------------------
# Standard site categories
# ---------------------------------------------------------------------------

CAT_MEJERI       = 'Køl'
CAT_KOED_FISK    = 'Kød & Fisk'
CAT_FRUGT_GROENT = 'Frugt & Grønt'
CAT_BROED_KAGER  = 'Brød & Kager'
CAT_FROST        = 'Frost'
CAT_KOLONIAL     = 'Kolonial'
CAT_DRIKKEVARER  = 'Drikkevarer'
CAT_SLIK         = 'Slik'
CAT_ANDET        = 'Andre varer'

# ---------------------------------------------------------------------------
# Subcategory keyword rules - ordered, first match wins
# ---------------------------------------------------------------------------

_SUBCATEGORY_RULES: dict[str, list[tuple[str, tuple]]] = {
    CAT_DRIKKEVARER: [
        ('Øl & Cider',        (' øl', 'øl ', 'pilsner', 'lager', ' ale ', 'ipa', 'stout', 'porter', 'cider', 'radler', 'breezer', 'pils ')),
        ('Vin & Spiritus',    ('hvidvin', 'rødvin', 'rosé', 'prosecco', 'champagne', 'cava', 'sangria', 'whisky', 'whiskey', 'vodka', ' gin ', ' rom ', 'tequila', 'likør', 'akvavit', 'spiritus', 'cognac', 'brandy', 'cointreau', 'baileys', ' vin ', 'vin,')),
        ('Kaffe & Te',        ('kaffe', 'espresso', 'cappuccino', 'kaffekapsler', 'nespresso', ' te ', 'te,', 'tebreve', 'chai', 'urtete', 'grøn te', 'matcha')),
        ('Juice & Smoothie',  ('juice', 'smoothie', 'nektar', 'frugtdrik', 'kokosvand')),
        ('Saft & Sirup',      ('saft', 'sirup', 'squash', 'koncentrat')),
        ('Vand',              ('mineralvand', 'kildevand', 'danskvand', ' vand', 'vand ')),
        ('Sodavand & Energi', ('cola', 'sodavand', 'energidrik', 'energy drink', 'sportsdrik', 'red bull', 'redbull', 'monster ', 'iste', 'ice tea', 'lemonade', 'tonic', 'kombucha')),
    ],
    CAT_MEJERI: [
        ('Mælk & Fløde',      ('mælk', 'fløde', 'halvfløde', 'kærnemælk', 'kefir', 'havremælk', 'mandelmælk', 'sojamælk', 'rismælk')),
        ('Yoghurt & Kvark',   ('yoghurt', 'skyr', 'kvark', 'ymer', 'fromage', 'fraiche', 'creme fraiche')),
        ('Ost',               (' ost', 'ost ', 'ost,', 'brie', 'camembert', 'gouda', 'cheddar', 'parmesan', 'fetaost', 'feta', 'mozzarella', 'ricotta', 'hytteost', 'danbo', 'esrom', 'castello')),
        ('Smør & Fedtstof',   ('smør', 'margarine', 'plantesmør', 'bregott', 'lurpak')),
        ('Pålæg & Kølvarer',  ('pålæg', 'leverpostej', 'postej', 'skinke', 'salami', 'rullepølse', 'spegepølse', 'mortadella', 'roastbeef', 'paté', 'pølse', 'hummus')),
        ('Æg',                ('æg',)),
    ],
    CAT_KOED_FISK: [
        ('Oksekød & Kalv',    ('okse', 'kalv', 'oksekød', 'entrecôte', 'ribeye', 'mørbrad', 'cuvette', 'oksesteg', 'tyksteg')),
        ('Svinekød',          ('svin', 'svinekød', 'nakkefilet', 'koteletter', 'flæsk', 'bacon', 'ribbensteg', 'svinesteg', 'svinemørbrad')),
        ('Fjerkræ',           ('kylling', 'kalkun', 'and ', 'ande', 'poussin')),
        ('Lam & Vildt',       ('lam', 'lammekød', 'vildt', 'hjort', 'rådyr', 'kanin')),
        ('Fisk & Skaldyr',    ('fisk', 'laks', 'torsk', 'tun', 'makrel', 'sild', 'rejer', 'muslinger', 'krabbe', 'blæksprutte', 'rødspætte', 'tilapia', 'pangasius', 'sei', 'kuller', 'ørred', 'aborre', 'helleflynder', 'hornfisk')),
        ('Pølser',            ('pølse', 'medister', 'grillpølse', 'hotdog', 'chorizo', 'pepperoni')),
    ],
    CAT_FRUGT_GROENT: [
        ('Frugt',         ('æble', 'pære', 'banan', 'appelsin', 'citron', 'lime', 'grape', 'melon', 'jordbær', 'hindbær', 'blåbær', 'mango', 'ananas', 'kiwi', 'fersken', 'nektarin', 'blomme', 'kirsebær', 'druer', 'avocado', 'kokos', 'papaya', 'klementin', 'mandarin', 'granatæble')),
        ('Grøntsager',    ('salat', 'spinat', 'grønkål', 'hvidkål', 'rødkål', 'broccoli', 'blomkål', 'gulerod', 'løg', 'kartofler', 'tomat', 'agurk', 'peberfrugt', 'zucchini', 'aubergine', 'selleri', 'fennikel', 'porrer', 'asparges', 'roer', 'radiser', 'majs', 'ærter', 'bønner', 'pastinak', 'rucola')),
        ('Svampe',        ('champignon', 'svampe', 'shiitake', 'portobello', 'østershat')),
        ('Krydderurter',  ('basilikum', 'persille', 'koriander', 'rosmarin', 'timian', 'mynte', 'estragon', 'oregano', 'dild', 'purløg', 'salvie')),
    ],
    CAT_BROED_KAGER: [
        ('Rugbrød & Knækbrød', ('rugbrød', 'knækbrød', 'rugmel')),
        ('Brød',               ('franskbrød', 'toastbrød', 'sandwichbrød', 'ciabatta', 'surdejsbrød', 'fuldkornsbrød', 'baguette', 'flutes', 'pita', 'focaccia', 'brød')),
        ('Boller',             ('boller', 'rundstykker', 'burgerboller', 'miniboller')),
        ('Kager & Wienerbrød', ('kage', 'wienerbrød', 'croissant', 'kanelsneglen', 'tebirkes', 'spandauer', 'muffin', 'tærte', 'lagkage', 'brownie', 'cheesecake', 'romkugle')),
        ('Kiks & Vafler',      ('kiks', 'crackers', 'vafler', 'riskager', 'digestive')),
        ('Bagning',            ('mel', 'hvedemel', 'gær', 'bagepulver', 'natron', 'majsstivelse')),
    ],
    CAT_FROST: [
        ('Is & Desserter',        ('is', 'flødeis', 'mælkeis', 'sorbetis', 'ispinde', 'islagkage', 'dessert', 'tiramisu', 'macarons', 'fondant', 'æbleskiver')),
        ('Frossen Fisk',          ('fisk', 'rejer', 'laks', 'torsk', 'rødspætte', 'sei', 'pangasius', 'tilapia', 'fiskepinde', 'panerede', 'tempura')),
        ('Frossen Kød',           ('kød', 'kylling', 'burger', 'bøf', 'frikadeller', 'kødboller', 'karbonader', 'hakket', 'pølse', 'medister')),
        ('Frossen Grønt & Frugt', ('ærter', 'majs', 'broccoli', 'spinat', 'bønner', 'grøntsags', 'edamame', 'mukimame', 'blåbær', 'jordbær', 'hindbær', 'brombær')),
        ('Frost Brød',            ('brød', 'boller', 'baguette', 'croissant', 'tebirkes', 'bagels', 'focaccia')),
        ('Færdigretter',          ('lasagne', 'pizza', 'tikka masala', 'butter chicken', 'boller i karry', 'spaghetti bolognese', 'karbonade', 'risotto', 'wok', 'gratin')),
    ],
    CAT_KOLONIAL: [
        ('Pasta & Ris',           ('pasta', 'spaghetti', 'penne', 'fusilli', 'rigatoni', 'lasagne plader', 'tagliatelle', 'fettuccine', 'nudler', 'macaroni', 'couscous', 'quinoa', 'bulgur', 'polenta', 'basmati', 'jasminris', 'risotto', ' ris ')),
        ('Konserves & Dåse',      ('dåse', 'konserves', 'kikærter', 'linser', 'kidneybønner', 'hvidebønner', 'flåede tomater', 'tomatpuré', 'rødbeder', 'sylte', 'syltede', 'majs', 'asparges', 'champignon', 'artiskok', 'dåseoliven', ' oliven ', 'sardiner', 'tun i ', 'makrel i ', 'ansjoser')),
        ('Morgenmad',             ('havregryn', 'müsli', 'granola', 'cornflakes', 'morgenmad', 'grød', 'chiafrø', 'hørfrø', 'fiberhusk')),
        ('Krydderier & Sauce',    ('krydderi', ' salt ', 'peber', 'chili', 'paprika', 'karry', 'sauce', 'ketchup', 'sennep', 'mayonnaise', 'dressing', 'bouillon', 'fond', 'soyasauce', 'pesto', 'sambal', 'tabasco', 'teriyaki')),
        ('Olie & Eddike',         ('olie', 'olivenolie', 'rapsolie', 'solsikkeolie', 'eddike', 'balsamico')),
        ('Nødder & Tørret Frugt', ('nødder', 'mandler', 'cashew', 'valnødder', 'hasselnødder', 'pistacier', 'jordnødder', 'rosiner', 'dadler', 'tørrede')),
        ('Bagning & Sødning',     ('mel ', 'sukker', 'melis', 'bagepulver', 'vanilje', 'honning', 'marmelade', 'syltetøj', 'nutella', 'peanutbutter', 'kakao', 'sødetabl', 'sødemiddel', 'stevia', 'sukrinol', 'canderel')),
        ('Supper & Snacks',       ('suppe', 'suppefond', 'popcorn', 'chips', 'nachos', 'kiks', 'cracker')),
    ],
    CAT_SLIK: [
        ('Chokolade',      ('chokolade', 'praliner', 'trøfler', 'bounty', 'snickers', 'twix', 'kit kat', 'mars', 'milka', 'toblerone', 'ferrero')),
        ('Slik & Vingummi',('vingummi', 'lakrids', 'skumfiduser', 'bolsjer', 'karameller', 'gummi', 'haribo', 'pastiller', 'tyggegummi', 'guf', 'skum')),
        ('Chips & Snacks', ('chips', 'popcorn', 'nachos', 'majschips', 'tortillachips', 'linsechips', 'jordnøddesnack')),
        ('Proteinbarer',   ('proteinbar', 'energibar', 'müslibar', 'snackbar', 'protein')),
    ],
}


def _get_subcategory(name: str, category: str) -> str:
    rules = _SUBCATEGORY_RULES.get(category)
    if not rules:
        return ''
    name_lower = name.lower()
    for sub_name, keywords in rules:
        if any(kw in name_lower for kw in keywords):
            return sub_name
    return 'Øvrige'


_UNIT_WORDS = {'g', 'kg', 'l', 'ml', 'cl', 'dl', 'stk', 'pak', 'ltr', 'pcs'}

# Ord der beskriver en vare uden at identificere HVAD den er. De skal ud af
# indholdsordene, ellers deler "Kyllingelårfilet u. skind" og "Laksefilet med
# skind" ordet 'skind' og bliver forvekslet som samme slags vare.
_GENERIC_WORDS = frozenset({
    'med', 'uden', 'fedt', 'pakke', 'store', 'stor', 'lille', 'frisk', 'friske',
    'dansk', 'danske', 'style', 'type', 'blandet', 'blandede', 'klassisk',
    'original', 'mini', 'skind', 'skiver', 'skaret', 'snittet', 'tern',
    'stykker', 'hele', 'halve', 'fyldte', 'fyldt', 'flere', 'varianter',
    'sorter', 'assorteret', 'økologisk', 'okologisk', 'eller',
    # Produktlinje-ord, som næsten alle brands bruger: "Senseo Classic" og
    # "Tuborg Classic" er ikke samme slags vare.
    'classic', 'classics', 'special', 'premium', 'family', 'selection',
})

_LEADING_DIGIT_RE = re.compile(r'^\d')


def product_content_words(name: str) -> set[str]:
    """Ord der siger hvad varen ER - til sammenligning af to varenavne.

    Tal, mål og rene beskrivelsesord luges ud, så det der er tilbage er
    varetypen og dens kendetegn ("flødeost", "opblanding", "spaghetti").
    Modsat _product_type_words (fjernet) springes første ord IKKE over: et
    brandnavn forrest gør ikke sidste ord til varetypen, og et navn på ét
    ord ("Peberkagefigurer") må ikke ende med en tom mængde - en tom mængde
    lod alle 1-2-ords-varer slippe forbi gaten hos den, der kaldte.
    """
    return {
        w for w in normalize_name(name).split()
        if len(w) >= 4 and not _LEADING_DIGIT_RE.match(w)
        and w not in _UNIT_WORDS and w not in _GENERIC_WORDS
    }


def variant_flags(name: str) -> tuple:
    """Variantmarkører der gør to ellers ens varer til forskellige varer."""
    return (is_organic(name), is_lactose_free(name), is_sugar_free(name),
            is_gluten_free(name), is_alcohol_free(name))


# ---------------------------------------------------------------------------
# Bilka category rules (keyword fallback)
# ---------------------------------------------------------------------------

_BILKA_CATEGORY_RULES = [
    (CAT_DRIKKEVARER,  ('cola', 'sodavand', 'juice', 'energidrik', 'øl', 'vin', 'spiritus', 'smoothie', 'vand', 'saft', 'cider', 'whisky', 'vodka', 'gin', 'rom', 'tequila', 'likør', 'akvavit', 'champagne', 'prosecco', 'cava', 'iste', 'sportsdrik', 'ingefærshot', 'kombucha', 'kokosvand', 'shots', 'frugtdrik', 'blanding', 'sirup', 'drik', 'lemonade', 'breezer', 'smirnoff', 'sangria', 'hvidvin', 'rødvin', 'rosévin', 'pilsner', 'bitter', 'tonic')),
    (CAT_FROST,        ('pommes frites', 'kyllingenuggets', 'frikadeller', 'flødeis', 'mælkeis', 'sorbetis', 'ispinde', 'isvafler', 'pizza m.', 'fuldkornsboller', 'håndværkere', 'miniflutes', 'croissanter', 'pain au chocolat', 'kanelsnegle', 'tebirkes', 'surdejsstykker', 'baguettes', 'focaccia m.', 'boller m.', 'bagels', 'grøntsagsblanding', 'bærblanding', 'blåbær', 'jordbær', 'hindbær', 'brombær', 'frys-selv', 'frossen', 'mukimame', 'edamame', 'kartoffelriste', 'kartoffelkroketter', 'løgringe', 'fiskepinde', 'panerede', 'rejenuggets', 'tempurarejer', 'butterfly rejer', 'vannamei rejer', 'grønlandske rejer', 'dumplings', 'gyoza', 'forårsruller', 'samosa', 'falafler', 'kødboller', 'melboller', 'karbonader', 'burgerbøffer', 'tikka masala m.', 'butter chicken m.', 'lasagne bolognese', 'spaghetti bolognese', 'karbonade m.', 'boller i karry m. ris', 'kylling i', 'flødeisvafler', 'mælkeis sandwich', 'limonadeis', 'islagkage', 'chokoladefondant', 'tiramisu', 'æbleskiver', 'æbleskiver m.', 'æblekage', 'skovbærtærte', 'citrontærte', 'cheesecake 2 stk', 'sacher 2 stk', 'tærte', 'macarons', 'pølsehorn', 'møllehjul', 'astronautis', "carte d'or")),
    (CAT_SLIK,         ('chips m.', 'majschips', 'linsechips', 'rodfrugtchips', 'popcorn', 'skumfiduser', 'vingummi', 'lakrids', 'chokoladebar', 'mælkechokolade', 'mørk chokolade', 'hvid chokolade', 'karameller', 'bolcher', 'pastiller', 'tyggegummi', 'müslibar', 'frugtsnacks', 'frugtstænger', 'rosiner', 'nøddeblanding', 'peanuts', 'flæskesvær', 'saltsnacks', 'saltstænger', 'marcipanbrød', 'vingummibamser', 'skumbananer', 'ostepops', 'dipmix', 'click mix', 'matador mix', 'stjerne mix', 'favorit mix', 'beef jerky', 'tørret mango', 'tørrede', 'rawbar', 'daddelbar', 'müslibarer', 'chokoladekugler', 'lakridsstænger', 'chips', 'osterejer', 'blandede chokolader')),
    # 'prince' i _BILKA_CATEGORY_RULES er kun til LU Prince-kiks - tobak fanget af is_age_restricted
    (CAT_BROED_KAGER,  ('rugbrød', 'toastbrød', 'sandwichbrød', 'burgerboller', 'hotdogbrød', 'pølsebrød', 'baguette', 'pitabrød', 'naanbrød', 'knækbrød', 'digestive kiks', 'mariekiks', 'havrekiks', 'kiks m.', 'cookies m.', 'kiks', 'lu prince', 'fuldkornsboller', 'solsikkeboller', 'rugboller', 'sandwichboller', 'hvedeboller', 'yoghurtboller', 'krydderboller', 'surdejsbrød', 'focaccia', 'ciabatta', 'grissini', 'rasp', 'tarteletter', 'lagkagebunde', 'tærtebund', 'vafler', 'isvafler', 'bondebrød', 'schwarzbrot', 'fladbrød', 'tortillas', 'tortillachips', 'pitabrød', 'fastelavnsbolle', 'boller', 'brød', 'bagels', 'citronmåne', 'romkugler', 'drømmekage', 'kanelstang', 'daim mini', 'mazarinkager', 'kammerjunkere', 'brownie', 'muffins', 'chokoladekage', 'citronkage', 'marmorkage', 'sandkage', 'gulerodskage', 'hindbærroulade', 'roulade', 'vaniljekranse', 'honningsnitter', 'småkager', 'tvebakker', 'pumpernickel', 'grovboller', 'proteinboller', 'proteinbrød', 'gulerodsboller', 'fuldkornssandwichbrød', 'skagensbrød', 'brioche', 'pølsehornsdej', 'pizzadej', 'butterdej', 'croissantdej', 'tærtedej', 'fuldkornspizzabunde', 'surdejspizzadej', 'surdejsboller')),
    (CAT_MEJERI,       ('mælk', 'smør', 'piskefløde', 'skyr', 'yoghurt', 'kefir', 'fraiche', 'creme fraiche', 'kærnemælk', 'ymer', 'bagegær', 'æg', 'havredrik', 'sojadrik', 'mandeldrik', 'risdrik', 'oatly', 'flydende til madlavning', 'stegemargarine', 'plantemargarine', 'smørbar', 'danbo', 'havarti', 'cheddar', 'mozzarella', 'brie', 'camembert', 'feta', 'gorgonzola', 'emmentaler', 'gouda', 'ricotta', 'mascarpone', 'burrata', 'parmesan', 'parmigiano', 'grana padano', 'pecorino', 'manchego', 'jarlsberg', 'samsø ost', 'danablu', 'blåskimmelost', 'rygeost', 'smøreost', 'flødeost', 'ostehaps', 'ostetern', 'salatost', 'hytteost', 'halloumi', 'gruyere', 'comté', 'port salut', 'præst', 'rødkitost')),
    (CAT_KOLONIAL,     ('pasta', 'ris', 'mel', 'sukker', 'olie', 'sauce', 'ketchup', 'marmelade', 'konserves', 'havregryn', 'müsli', 'musli', 'granola', 'bouillon', 'krydderi', 'sennep', 'mayonnaise', 'remoulade', 'dressing', 'tun i', 'makrel i', 'sardiner', 'oliven', 'kapers', 'pesto', 'tomatsauce', 'passata', 'hakkede tomater', 'tomatpuré', 'pizzasauce', 'bechamelsauce', 'hollandaise', 'bearnaisesauce', 'honning', 'sirup', 'eddike', 'cornflakes', 'frosties', 'coco pops', 'cheerios', 'havrefras', 'fiberknas', 'guldkorn', 'risottoris', 'basmatiris', 'jasminris', 'parboiled', 'fusilli', 'spaghetti', 'penne', 'lasagneplader', 'tagliatelle', 'gnocchi', 'instant kaffe', 'formalet kaffe', 'hele bønner', 'kaffekapsler', 'te', 'bagepulver', 'vaniljesukker', 'chiafrø', 'hørfrø', 'solsikkekerner', 'valnødder', 'cashewnødder', 'mandler', 'pinjekerner', 'pistaciekerner', 'kokosmel', 'kokosmælk', 'sojasauce', 'woksauce', 'tortillas', 'tacosauce', 'tortillachips', 'nudler', 'risnudler', 'hvedenudler', 'glasnudler', 'chilisauce', 'teriyaki', 'boller i karry', 'lasagne', 'spaghetti bolognese', 'pasta carbonara', 'burger', 'frokostplatte', 'kylling tikka masala', 'tikka masala', 'butter chicken', 'tarteletfyld', 'biksemad', 'millionbøf', 'flæskestegsburger', 'schnitzel m. tilbehør', 'karbonader m.', 'frikadeller m.', 'hakkebøffer m.', 'kartoffelmos m.', 'boller i karry m.', 'kylling i karry', 'kylling i rød', 'kylling m. ris', 'pasta m. kylling', 'pasta bolognese', 'mørbradgryde', 'paprikagryde', 'goulash', 'forloren hare', 'wienergryde', 'jægergryde', 'gyros m.', 'kyllingewok', 'ris m. kylling', 'risotto m.')),
    (CAT_FRUGT_GROENT, ('agurk', 'bananer', 'banan', 'peberfrugt', 'tomat', 'gulerødder', 'gulerod', 'salat', 'broccoli', 'blomkål', 'æbler', 'æble', 'pærer', 'pære', 'appelsin', 'citron', 'jordbær', 'hindbær', 'kål', 'rødkål', 'hvidkål', 'spidskål', 'løg', 'rødløg', 'forårsløg', 'kartofler', 'kartoffel', 'squash', 'avocado', 'spinat', 'svampe', 'champignon', 'melon', 'druer', 'mango', 'ananas', 'blåbær', 'brombær', 'solbær', 'tranebær', 'klementiner', 'kiwi', 'lime', 'citrongræs', 'ingefær', 'hvidløg', 'purløg', 'persille', 'dild', 'basilikum', 'rosmarin', 'timian', 'asparges', 'artiskok', 'selleri', 'pastinak', 'persillerod', 'rødbeder', 'jordskokkerne', 'aubergine', 'courgette', 'rosenkål', 'grønkål', 'rucola', 'feldsalat', 'icebergsalat', 'romainesalat', 'pak choi', 'sugarsnaps', 'ærter', 'bobbybønner', 'sukkerærter', 'vandmelon', 'papaya', 'dadler', 'figner', 'granatæble', 'coconut', 'passionsfrugt', 'mandariner', 'klementiner', 'nektariner', 'abrikoser', 'blomme', 'kirsebær', 'vindruer', 'hokkaido', 'butternut')),
]


@lru_cache(maxsize=1)
def _search_category_priors() -> dict[str, str]:
    """Søgeord → forventet kategori, udledt af _BILKA_CATEGORY_RULES.

    Genbruger den kuraterede tabel unify_category allerede bruger, i stedet for
    endnu en håndlavet ordliste der kan drive fra den. Kun ÉT-ORDS nøgleord
    tages med: søgetermer er enkelt-tokens efter normalize_name, så
    flerords-nøgleord ("creme fraiche", "chips m.") kan alligevel ikke matche.

    Nøgleord der optræder under FLERE kategorier droppes helt (værdi = ''):
    er ordet tvetydigt, er en prior et gæt, og et forkert gæt ville rykke de
    rigtige varer NED. Bedre ingen prior end en forkert.
    """
    seen: dict[str, str] = {}
    for category, keywords in _BILKA_CATEGORY_RULES:
        for kw in keywords:
            token = _fold(normalize_name(kw))
            if not token or ' ' in token:
                continue
            if token in seen and seen[token] != category:
                seen[token] = ''      # tvetydigt - ingen prior
            else:
                seen.setdefault(token, category)
    return {k: v for k, v in seen.items() if v}


def _search_term_category(terms: tuple[str, ...]) -> str:
    """Den kategori søgeordene peger på, eller '' hvis de ikke peger entydigt.

    Peger to termer på hver sin kategori (fx "mælk chokolade"), giver vi op -
    så er der ingen entydig forventning at løfte efter.
    """
    priors = _search_category_priors()
    found = {priors[f] for f in (_fold(t) for t in terms) if f in priors}
    return found.pop() if len(found) == 1 else ''


def _compile_bilka_rule(keywords: tuple[str, ...]) -> re.Pattern:
    """Ordgrænset (kun til højre) match af en kategori-nøgleordsliste.

    Den gamle `any(kw in name for kw in keywords)` var ren substring-
    matching: den korte nøgleord 'rom' matchede inde i "romainesalat", så
    fase-2-kortet "Romaine salat" kunne vises under Drikkevarer i stedet
    for Frugt & grønt, hver gang det tilfældigt blev Bilka-fronteret (se
    matchmotor-revisionen 2026-08-16, fund C5).

    Grænse KUN til højre - samme valg og begrundelse som
    scraper/keywords.py's NON_FOOD_KEYWORDS (og _NON_FOOD_NAME_RE ovenfor,
    fund H2): danske sammensætninger sætter kernen sidst ("proteindrik",
    "kokosdrik"), så et krav om ordstart til venstre havde IKKE fanget
    "Romaine" (den fejler stadig på højregrænsen, "rom" følges af "aine"),
    men ville til gengæld have fejlagtigt afvist "drik" i "Proteindrik" -
    et bekræftet regressionsfund under selve rettelsen af C5. Højre-only
    grænse løser begge sager korrekt.
    """
    parts = sorted((re.escape(k) for k in keywords), key=len, reverse=True)
    return re.compile(
        r'(?:' + '|'.join(parts) + r')(?![0-9a-zæøåäöü])',
        re.IGNORECASE,
    )


_BILKA_CATEGORY_RULES_COMPILED = [
    (cat_const, _compile_bilka_rule(keywords))
    for cat_const, keywords in _BILKA_CATEGORY_RULES
]


def unify_category(raw_cat, product_name='', brand=''):
    """Maps any store category or product name to a standard website category.

    Returnerer None hvis varen ikke er mad (tobak, bleer, pleje, …) -
    så filtreres den fra på hjemmesiden og i matching. Alkohol er OK.
    """
    raw = str(raw_cat or '').lower().strip()
    name = str(product_name or '').lower().strip()
    brand_s = str(brand or '').strip()

    # Tobak/nikotin (titel + brand; Rema-ID via is_age_restricted)
    if is_age_restricted(product_name, brand_s, raw_cat):
        return None

    # LU Prince-kiks (ikke tobak) - før non-food-navnefilter, da "prince"
    # ellers kan ramme cigaretnavne i den delte termliste.
    if 'prince' in name and ('kiks' in name or 'lu' in name or 'cookie' in name
                             or 'chokolade' in name or 'creme' in name
                             or _LU_PRINCE_COOKIE_RE.search(name)):
        return CAT_BROED_KAGER

    # Krav: kun mad - ingen undtagelser. Klart ikke-mad (navn) frasorteres straks.
    if name and _NON_FOOD_NAME_RE.search(name):
        return None
    # Brand-feltet: fang tobakspakker (HARDBOX) og babypleje (Libero/Huggies) m.m.
    if brand_s and _NON_FOOD_NAME_RE.search(brand_s.lower()):
        if not _LU_PRINCE_COOKIE_RE.search(f'{name} {brand_s}'.lower()):
            return None

    if 'lolly' in name or 'frys-selv' in name or 'ispind' in name:
        return CAT_FROST

    if 'kiosk' in raw and name:
        _kiosk_drink = ('cola', 'sodavand', 'juice', 'energidrik', 'energy drink', 'øl', 'vin', 'cider', 'vand', 'saft', 'iste', 'ice tea', 'sportsdrik', 'kombucha', 'drik', 'lemonade', 'shots', 'smoothie', 'frugtdrik', 'breezer', 'kokosvand')
        _kiosk_slik  = ('chips', 'popcorn', 'nachos', 'majschips', 'tortillachips', 'chokolade', 'slik', 'vingummi', 'lakrids', 'skumfiduser', 'bolsjer', 'karameller', 'nødder', 'jordnødder', 'guf', 'tyggegummi', ' gum', 'gum ', 'skum', 'orbit', 'stimorol', 'dirol', 'mentos', 'hubba bubba', 'wrigley')
        _kiosk_mejeri= ('coleslaw', 'waldorf', 'hummussalat', 'pastasalat', 'kartoffelsalat', 'grøn salat', 'salat ')
        if any(kw in name for kw in _kiosk_drink):  return CAT_DRIKKEVARER
        if any(kw in name for kw in _kiosk_slik):   return CAT_SLIK
        if any(kw in name for kw in _kiosk_mejeri): return CAT_MEJERI

    mapping = {
        'mejeri': CAT_MEJERI, 'mejeriprodukter & kølvarer': CAT_MEJERI,
        'pålæg og kølede middagsretter': CAT_MEJERI, 'køl': CAT_MEJERI,
        'ost': CAT_MEJERI, 'ost m.v.': CAT_MEJERI,
        'kød': CAT_KOED_FISK, 'fisk og skaldyr': CAT_KOED_FISK,
        'kød, fisk & fjerkræ': CAT_KOED_FISK, 'kød fisk fjerkræ': CAT_KOED_FISK,
        'frugt & grønt': CAT_FRUGT_GROENT, 'frugt og grønt': CAT_FRUGT_GROENT,
        'brød & kager': CAT_BROED_KAGER, 'brød og kager': CAT_BROED_KAGER,
        'brød & bavinchi': CAT_BROED_KAGER,
        'frost': CAT_FROST,
        'kolonial': CAT_KOLONIAL, 'kolonialvarer': CAT_KOLONIAL,
        'drikkevarer': CAT_DRIKKEVARER, 'vin og spiritus': CAT_DRIKKEVARER,
        'personlig pleje': None, 'pleje': None, 'husholdning': None,
        'rengøring': None, 'baby og småbørn': None,
        'kiosk': CAT_DRIKKEVARER, 'kiosk - slik og snack - chips og snacks': CAT_SLIK,
        'slik': CAT_SLIK, 'slik & snacks': CAT_SLIK, 'slik og snacks': CAT_SLIK,
        'kiosk - slik og snack - chokolade': CAT_SLIK, 'kiosk - slik og snack - slik': CAT_SLIK,
        'frugt-og-groent': CAT_FRUGT_GROENT, 'mejeri-og-koel': CAT_MEJERI,
        'slik-og-snacks': CAT_SLIK, 'broed-og-kager': CAT_BROED_KAGER,
        'koed-og-fisk': CAT_KOED_FISK, 'mad-fra-hele-verden': CAT_KOLONIAL,
        'ispinde-og-sodavandsis': CAT_FROST, 'is-i-baeger': CAT_FROST,
        'frys-selv-is': CAT_FROST, 'isvafler': CAT_FROST,
        'desserter-og-islagkager': CAT_FROST, 'groentsager': CAT_FROST,
        'faerdigretter-paa-frost': CAT_FROST, 'frugt-og-baer': CAT_FROST,
        'kartofler-og-pommes-frites': CAT_FROST,
        'avis': CAT_ANDET,
    }
    # Idempotens: funktionen kaldes flere steder med en værdi den SELV har
    # produceret - fx updater.py's Rema-annotering, der sender det allerede
    # mappede '/product/product_type' ind igen. De fleste kanoniske navne stod
    # allerede i tabellen ('køl', 'frost', 'slik' ...), men 'Kød & Fisk' og
    # 'Andre varer' gjorde ikke. De faldt derfor igennem til default'en
    # CAT_KOLONIAL, hvilket slog type-gaten ud for ALLE Rema-kød/fisk-varer:
    # gaten krævede i stedet 0,80 navnescore, og match-raten for Kød & Fisk
    # endte på 30% mod 53-74% i de øvrige kategorier.
    # Bygges af konstanterne, så tabellen ikke kan drifte fra dem igen.
    for _canon in (CAT_MEJERI, CAT_KOED_FISK, CAT_FRUGT_GROENT, CAT_BROED_KAGER,
                   CAT_FROST, CAT_KOLONIAL, CAT_DRIKKEVARER, CAT_SLIK, CAT_ANDET):
        mapping.setdefault(_canon.lower(), _canon)

    if raw in mapping:
        return mapping[raw]
    for cat_const, pattern in _BILKA_CATEGORY_RULES_COMPILED:
        if pattern.search(name):
            return cat_const
    return CAT_KOLONIAL if raw else CAT_ANDET


# ---------------------------------------------------------------------------
# Product display helpers
# ---------------------------------------------------------------------------

def parse_sale_end_date(product: dict) -> str | None:
    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
    if len(sale_dates) <= 1:
        return None
    try:
        date_obj = datetime.strptime(sale_dates[1].strip(), '%Y-%m-%dT%H:%M:%S%z')
        return date_obj.strftime('%d/%m')
    except (ValueError, TypeError):
        return None


def product_to_display_dict(
    product: dict,
    *,
    category: str | None = None,
    sale_end_date: str | None = None,
    default_category: str = 'Andre varer',
    force_sale: bool = False,
) -> dict:
    sale_price = product.get('/product/sale_price')
    ptype = category or product.get('/product/product_type') or default_category
    name_str = str(product.get('/product/title', 'Ukendt vare'))
    unit_measure = str(product.get('/product/unit_pricing_measure', '') or '')
    is_sale = force_sale or sale_price is not None
    weight_g = parse_weight_to_grams(unit_measure)
    if weight_g is None:
        try:
            weight_g = float(product.get('/product/weight_g'))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            weight_g = None
    result = {
        'id': str(product.get('/product/id', '')),
        'name': name_str,
        'price': float(product.get('/product/price', 0)),
        'sale_price': float(sale_price) if sale_price is not None else None,
        'description': str(product.get('/product/description', '')),
        'category': str(ptype),
        'brand': str(product.get('/product/brand', '')),
        'image_url': str(product.get('/product/imageLink', '')),
        'rema_image': product.get('/product/rema_image', ''),
        'is_sale': is_sale,
        'is_any_sale': product.get('/product/is_any_sale', False),
        'sale_end_date': sale_end_date if sale_end_date is not None else parse_sale_end_date(product),
        'store': str(product.get('/product/store', 'Rema 1000')),
        'unit_measure': unit_measure,
        # Samme fallback som scripts/seed-d1.py::build_row_values - uden den
        # mistede display-dict'en vægten for produkter hvor kun det rå
        # /product/weight_g-felt (ikke unit_pricing_measure) findes, mens D1's
        # egen weight_g-kolonne (brugt til SQL-filtrering) alligevel havde den.
        'weight_g': weight_g,
        'stk_count': product.get('/product/stk_count') or parse_stk_count(unit_measure),
        'price_per_kg': product.get('/product/price_per_kg'),
        'store_matches': product.get('/product/store_matches', {}),
        'cheaper_at': product.get('/product/cheaper_at'),
        'cheapest_at': product.get('/product/cheapest_at'),
        'rema_price': product.get('/product/rema_price'),
        'rema_is_sale': product.get('/product/rema_is_sale'),
        'multi_deal': product.get('/product/multi_deal', ''),
        'lowest_price_30d': product.get('/product/lowest_price_30d'),
        # subcategory/is_organic/is_lactose_free er præcomputeret ved nattens
        # seed (scripts/seed-d1.py, samme kolonner som SQL-filtrene bruger) -
        # slås op her i stedet for at genberegnes for hvert produkt på hver
        # side (forside/kategori/tilbud/søgning). _get_subcategory scanner op
        # til 100+ nøgleord pr. kald, så dette rammer alle sider, ikke kun
        # søgningens kandidatpulje (samme klasse fund som _flavor_field
        # nedenfor, 2026-08-05). Manglende felt (ældre cache, eller lokal/
        # ikke-D1-tilstand) falder blødt tilbage til live-beregning.
        # Korrekthed: subcategory er kun gyldig hvis den er beregnet ud fra
        # samme category som `ptype` her - sikret ved at kategori-siden
        # filtrerer D1-rækker på category = actual_category i SQL'en FØR
        # product_to_display_dict kaldes (app.py::build_category_listing).
        'subcategory': product.get('/product/subcategory') or _get_subcategory(name_str, str(ptype)),
        # Forudberegnede filtreringsfelter - bruges af product_card.html som
        # data-is-organic / data-is-lactose-free. Python-versionen fanger
        # kanttilfælde som startswith('øko') og lacto-varianter, som den
        # tidligere Jinja-inline-udgave gik glip af.
        'is_organic': product.get('/product/is_organic') if '/product/is_organic' in product else is_organic(name_str, str(product.get('/product/description', '')), str(product.get('/product/brand', ''))),
        'is_lactose_free': product.get('/product/is_lactose_free') if '/product/is_lactose_free' in product else is_lactose_free(name_str, str(product.get('/product/description', '')), str(product.get('/product/brand', ''))),
    }
    # Præcomputeret smagsfelt fra nattens seed (scripts/seed-d1.py) - se
    # _product_flavor_search_field. Sættes KUN når det rå produkt faktisk bar
    # feltet: en tom streng er et gyldigt præberegnet svar (de fleste varer
    # har ingen smagsord), så nøglens tilstedeværelse - ikke dens værdi - er
    # det der skelner "præberegnet" fra "cache fra før seed'et", hvor der skal
    # falles tilbage til live-beregning.
    if '/product/flavor_kw' in product:
        result['_flavor_field'] = product['/product/flavor_kw'] or ''
    if not is_sale:
        result['sale_end_date'] = sale_end_date
    return result


def _serialize_store_match(match: dict) -> dict:
    """Ét store_matches-entry til native JSON (docs/native-app.md §3.2)."""
    if not isinstance(match, dict):
        return {}
    kg = match.get('kg_price')
    try:
        kg_price = float(kg) if kg is not None and kg != '' else None
    except (TypeError, ValueError):
        kg_price = None
    price = match.get('price')
    normal = match.get('normal_price')
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    try:
        normal_f = float(normal) if normal is not None else None
    except (TypeError, ValueError):
        normal_f = None
    return {
        'name': str(match.get('name') or ''),
        'price': price_f,
        'normal_price': normal_f,
        'is_sale': bool(match.get('is_sale')),
        'image': str(match.get('image') or ''),
        'brand': str(match.get('brand') or ''),
        'description': str(match.get('description') or ''),
        'weight': str(match.get('weight') or ''),
        'kg_price': kg_price,
        'multi_deal': str(match.get('multi_deal') or ''),
        'ean': str(match.get('ean') or ''),
        'Kategori': str(match.get('Kategori') or ''),
    }


def product_to_api_dict(display: dict) -> dict:
    """Native listing-JSON fra et product_to_display_dict-resultat.

    Spejler product_card-data-* (docs/native-app.md §3): price er effektiv
    (tilbud hvis aktiv), normal_price er listepris. has_match / has_match_rema
    følger samme betingelser som makroen.
    """
    is_sale = bool(display.get('is_sale'))
    list_price = display.get('price')
    sale_price = display.get('sale_price')
    try:
        normal_price = float(list_price) if list_price is not None else 0.0
    except (TypeError, ValueError):
        normal_price = 0.0
    if is_sale and sale_price is not None:
        try:
            price = float(sale_price)
        except (TypeError, ValueError):
            price = normal_price
    else:
        price = normal_price

    rem_raw = display.get('rema_price')
    try:
        rem_price = float(rem_raw) if rem_raw is not None and rem_raw != '' else 0.0
    except (TypeError, ValueError):
        rem_price = 0.0

    store_matches_raw = display.get('store_matches') or {}
    if not isinstance(store_matches_raw, dict):
        store_matches_raw = {}
    store_matches = {
        str(key): _serialize_store_match(match)
        for key, match in store_matches_raw.items()
        if isinstance(match, dict)
    }

    image = str(display.get('image_url') or '')
    kg = display.get('price_per_kg')
    try:
        kg_price = float(kg) if kg is not None and kg != '' else None
    except (TypeError, ValueError):
        kg_price = None

    return {
        'id': str(display.get('id') or ''),
        'name': str(display.get('name') or ''),
        'brand': str(display.get('brand') or ''),
        'description': str(display.get('description') or ''),
        'image': image,
        'main_image': image,
        'rema_image': str(display.get('rema_image') or ''),
        'category': str(display.get('category') or 'Andre varer'),
        'subcategory': str(display.get('subcategory') or ''),
        'store': str(display.get('store') or 'Rema 1000'),
        'price': price,
        'normal_price': normal_price,
        'is_sale': is_sale,
        'is_any_sale': bool(display.get('is_any_sale')),
        'sale_end_date': display.get('sale_end_date'),
        'unit_measure': str(display.get('unit_measure') or ''),
        'weight_g': display.get('weight_g'),
        'stk_count': display.get('stk_count'),
        'kg_price': kg_price,
        'multi_deal': str(display.get('multi_deal') or ''),
        'is_organic': bool(display.get('is_organic')),
        'is_lactose_free': bool(display.get('is_lactose_free')),
        'has_match': bool(store_matches) or rem_price > 0,
        'has_match_rema': rem_price > 0,
        'cheapest_at': display.get('cheapest_at') or None,
        'cheaper_at': display.get('cheaper_at') or None,
        'rema_price': rem_price if rem_price > 0 else None,
        'rema_is_sale': bool(display.get('rema_is_sale')),
        'lowest_price_30d': display.get('lowest_price_30d'),
        'store_matches': store_matches,
    }


def products_to_api_list(products: list) -> list:
    return [product_to_api_dict(p) for p in products]


def product_available_at_active_stores(product: dict, active_stores: set | None) -> bool:
    if active_stores is None:
        return True
    if len(active_stores) == 0:
        return False
    display_store = product.get('/product/store', 'Rema 1000')
    if display_store in active_stores:
        return True
    if 'Rema 1000' in active_stores and product.get('/product/rema_price'):
        return True
    for key in (product.get('/product/store_matches') or {}):
        label = _STORE_CONFIGS.get(key, {}).get('label')
        if label in active_stores:
            return True
    return False


def _promote_match_to_product(product: dict, store_key: str, match: dict) -> dict:
    out = dict(product)
    out['/product/title'] = match['name']
    out['/product/store'] = _STORE_CONFIGS[store_key]['label']
    if match.get('is_sale'):
        out['/product/price'] = match.get('normal_price') or match['price']
        out['/product/sale_price'] = match['price']
    else:
        out['/product/price'] = match['price']
        out['/product/sale_price'] = None
    if match.get('image') and str(match['image']).lower() != 'nan':
        out['/product/imageLink'] = match['image']
    out['/product/brand'] = match.get('brand') or ''
    out['/product/description'] = match.get('description') or ''
    out['/product/unit_pricing_measure'] = match.get('weight') or out.get('/product/unit_pricing_measure')
    out['/product/price_per_kg'] = match.get('kg_price')
    out['/product/multi_deal'] = match.get('multi_deal', '')
    out['/product/cheapest_at'] = store_key
    new_type = unify_category(match.get('Kategori', ''), match['name'], match.get('brand', ''))
    if new_type and new_type != CAT_ANDET:
        out['/product/product_type'] = new_type
    return out


def product_for_active_stores(product: dict, active_stores: set | None) -> dict | None:
    if not product_available_at_active_stores(product, active_stores):
        return None
    if active_stores is None or 'Rema 1000' in active_stores:
        return product
    display_store = product.get('/product/store', 'Rema 1000')
    if display_store in active_stores:
        return product
    matches = product.get('/product/store_matches') or {}
    best_key = None
    best_price = None
    for key, match in matches.items():
        label = _STORE_CONFIGS.get(key, {}).get('label')
        if label not in active_stores:
            continue
        try:
            price = float(match.get('price', 0))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if best_price is None or price < best_price:
            best_price = price
            best_key = key
    if best_key:
        return _promote_match_to_product(product, best_key, matches[best_key])
    return None
