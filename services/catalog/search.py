"""Catalog search: the legacy LIKE '%q%' contract and the enhanced Thai-aware ranker.

Both are pure functions over product dicts so the same code is exercised by the
service (`SEARCH_MODE=legacy|enhanced`) and by `tools/search_eval.py`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

# Thai tone marks plus thanthakhat/maitaikhu, which shoppers drop or reorder when typing.
# Nikhahit (U+0E4D) is kept because it spells sara am (ำ) when followed by sara aa.
THAI_TONE_MARKS = re.compile("[\u0e48-\u0e4c\u0e47]")
SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFC, casefold, strip tone marks, unify sara am spellings, collapse whitespace."""
    text = unicodedata.normalize("NFC", text).casefold()
    text = THAI_TONE_MARKS.sub("", text)
    text = text.replace("\u0e33", "\u0e4d\u0e32")
    return SPACES.sub(" ", text).strip()


def legacy_matches(query: str, product: Mapping[str, Any]) -> bool:
    """`name LIKE '%q%' OR category LIKE '%q%'` with MySQL's case-insensitive collation."""
    q = query.strip().casefold()
    return q in str(product["name"]).casefold() or q in str(product["category"]).casefold()


def legacy_search(query: str, products: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(p) for p in products if legacy_matches(query, p)]


def _tokens(text: str) -> list[str]:
    return [token for token in normalize(text).split(" ") if token]


def score(query: str, product: Mapping[str, Any], synonyms: Mapping[str, list[str]]) -> float:
    """Deterministic relevance: exact > prefix > substring > synonym, name > tags > category."""
    q = normalize(query)
    if not q:
        return 0.0
    name = normalize(str(product["name"]))
    category = normalize(str(product["category"]))
    tags = [normalize(t) for t in product.get("tags", [])]
    description = normalize(str(product.get("description", "")))

    total = 0.0
    if q == name:
        total += 10
    elif name.startswith(q):
        total += 6
    elif q in name:
        total += 4
    if any(q == t for t in tags):
        total += 5
    elif any(t.startswith(q) for t in tags):
        total += 3
    if q == category or category.startswith(q):
        total += 2
    if q in description:
        total += 1

    for token in _tokens(query):
        for alias in synonyms.get(token, []):
            alias = normalize(alias)
            if alias == name or alias in tags:
                total += 5
            elif alias in name or alias in description:
                total += 3
    return total


def enhanced_search(
    query: str,
    products: Iterable[Mapping[str, Any]],
    synonyms: Mapping[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Ranked, tone-mark-insensitive, synonym- and prefix-aware search. Ties break on id."""
    synonyms = {normalize(k): v for k, v in (synonyms or {}).items()}
    scored = [(score(query, p, synonyms), int(p["id"]), dict(p)) for p in products]
    return [p for s, _, p in sorted(scored, key=lambda t: (-t[0], t[1])) if s > 0]
