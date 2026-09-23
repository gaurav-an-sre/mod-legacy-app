"""Sawan Mart search evaluation: the enhanced ranker must beat legacy LIKE without regressing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "services" / "catalog"))

from search import enhanced_search, legacy_search, normalize  # noqa: E402

from tools.search_eval import FIXTURE_DIR, evaluate  # noqa: E402

PRODUCTS = yaml.safe_load((FIXTURE_DIR / "products.yaml").read_text(encoding="utf-8"))
SYNONYMS = yaml.safe_load((FIXTURE_DIR / "synonyms.yaml").read_text(encoding="utf-8"))
GOLDEN = yaml.safe_load((FIXTURE_DIR / "golden.yaml").read_text(encoding="utf-8"))


def test_normalize_is_tone_mark_and_sara_am_insensitive() -> None:
    assert normalize("น้ำปลา") == normalize("นำปลา") == normalize("นํ้าปลา")
    assert normalize("Coke ") == "coke"


def test_legacy_search_is_plain_substring_match() -> None:
    assert [p["id"] for p in legacy_search("น้ำปลา", PRODUCTS)] == [1]
    assert legacy_search("โค้ก", PRODUCTS) == []


def test_enhanced_search_ranks_synonyms_and_prefixes() -> None:
    assert enhanced_search("โค้ก", PRODUCTS, SYNONYMS)[0]["id"] == 3
    assert [p["id"] for p in enhanced_search("นม", PRODUCTS, SYNONYMS)[:2]] == [6, 7]


def test_golden_report_passes_and_is_deterministic(tmp_path: Path) -> None:
    first = evaluate(PRODUCTS, SYNONYMS, GOLDEN)
    second = evaluate(PRODUCTS, SYNONYMS, GOLDEN)
    assert first == second
    assert first["passed"], first["summary"]
    assert first["regressions"] == []
    assert first["summary"]["enhanced"]["overall"] > first["summary"]["legacy"]["overall"]
    json.dumps(first, ensure_ascii=False)


def test_committed_report_matches_fixtures() -> None:
    committed = json.loads((ROOT / "search_eval" / "catalog.json").read_text(encoding="utf-8"))
    assert committed["summary"] == evaluate(PRODUCTS, SYNONYMS, GOLDEN)["summary"]
