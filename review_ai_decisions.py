"""
CLI-værktøj til at reviewe AI-afgørelser fra produktklassificering.

Brug: python review_ai_decisions.py

Kommandoer pr. produkt:
  Enter  – accepter AI's afgørelse (spring over)
  n      – AI tog fejl (noterer, ingen ændring i lister)
  s      – tilføj et ord til sortlisten (NON_FOOD_KEYWORDS)
  h      – tilføj et ord til hvidlisten (FOOD_KEYWORDS)
  q      – afslut
"""

import csv
import os
import sys

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_FILE = os.path.join(_ROOT_DIR, 'data', 'ai_decisions.csv')
_EXTRA_BLOCKED = os.path.join(_ROOT_DIR, 'data', 'extra_blocked_keywords.txt')
_EXTRA_FOOD = os.path.join(_ROOT_DIR, 'data', 'extra_food_keywords.txt')
_REVIEWED_FILE = os.path.join(_ROOT_DIR, 'data', 'ai_decisions_reviewed.csv')


def _append_keyword(path: str, keyword: str) -> None:
    keyword = keyword.strip().lower()
    if not keyword:
        return
    # Undgå dubletter
    existing: set[str] = set()
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            existing = {line.strip().lower() for line in f if line.strip()}
    if keyword in existing:
        print(f'  "{keyword}" findes allerede i filen.')
        return
    with open(path, 'a', encoding='utf-8') as f:
        f.write(keyword + '\n')
    print(f'  Tilføjet: "{keyword}"')


def _load_decisions() -> list[dict]:
    if not os.path.isfile(_LOG_FILE):
        print(f'Ingen logfil fundet: {_LOG_FILE}')
        sys.exit(0)
    with open(_LOG_FILE, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _already_reviewed() -> set[str]:
    """Returnerer et set af (product_name, category) tuples der allerede er reviewet."""
    if not os.path.isfile(_REVIEWED_FILE):
        return set()
    with open(_REVIEWED_FILE, newline='', encoding='utf-8') as f:
        return {(row['product_name'], row['category']) for row in csv.DictReader(f)}


def _save_reviewed(row: dict, verdict: str) -> None:
    file_exists = os.path.isfile(_REVIEWED_FILE)
    with open(_REVIEWED_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['timestamp', 'product_name', 'category', 'ai_decision', 'verdict'])
        writer.writerow([
            row['timestamp'], row['product_name'], row['category'],
            row['decision'], verdict,
        ])


def main() -> None:
    decisions = _load_decisions()
    reviewed = _already_reviewed()

    pending = [
        d for d in decisions
        if (d['product_name'], d['category']) not in reviewed
    ]

    if not pending:
        print('Alle AI-afgørelser er allerede reviewet.')
        return

    print(f'=== Review af AI-afgørelser ({len(pending)} tilbage) ===')
    print('Enter=OK  n=fejl  s=tilføj sortliste  h=tilføj hvidliste  q=afslut\n')

    for i, row in enumerate(pending, 1):
        name = row['product_name']
        category = row.get('category', '')
        decision = row['decision']

        decision_label = 'FØDEVARE ✓' if decision == 'JA' else 'IKKE FØDEVARE ✗'
        cat_str = f'  [{category}]' if category else ''
        print(f'[{i}/{len(pending)}] {name}{cat_str}  →  AI: {decision_label}')

        try:
            choice = input('> ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('\nAfbrudt.')
            break

        if choice == 'q':
            print('Afslutter.')
            break
        elif choice == 'n':
            _save_reviewed(row, 'FORKERT')
            print('  Markeret som forkert.')
        elif choice == 's':
            default = name.lower()
            kw = input(f'  Søgeord til sortliste [{default}]: ').strip() or default
            _append_keyword(_EXTRA_BLOCKED, kw)
            _save_reviewed(row, f'SORTLISTE:{kw}')
        elif choice == 'h':
            default = name.lower()
            kw = input(f'  Søgeord til hvidliste [{default}]: ').strip() or default
            _append_keyword(_EXTRA_FOOD, kw)
            _save_reviewed(row, f'HVIDLISTE:{kw}')
        else:
            # Enter eller andet = accepter
            _save_reviewed(row, 'OK')

        print()

    print('Done. Genstart scrapers for at aktivere nye søgeord.')


if __name__ == '__main__':
    main()
