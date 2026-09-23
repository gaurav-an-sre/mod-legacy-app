"""Deterministic search evaluation: legacy LIKE contract vs the enhanced ranker.

Reads a fixture catalog, synonyms and golden queries, computes recall@k per query
and per failure-mode category for both engines, and writes search_eval/<slice>.json.
Exit 1 if the enhanced engine is below threshold or regresses on any category.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "catalog"))

from search import enhanced_search, legacy_search  # noqa: E402

FIXTURE_DIR = ROOT / "search_eval" / "sawan_mart"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def recall_at_k(results: list[dict[str, Any]], expect: list[int], k: int) -> float:
    top = {int(p["id"]) for p in results[:k]}
    return len(top & set(expect)) / len(expect) if expect else 1.0


def evaluate(
    products: list[dict[str, Any]],
    synonyms: dict[str, list[str]],
    golden: dict[str, Any],
    slice_name: str = "catalog",
) -> dict[str, Any]:
    k = int(golden.get("k", 3))
    threshold = float(golden.get("threshold", 0.9))
    engines = {
        "legacy": lambda q: legacy_search(q, products),
        "enhanced": lambda q: enhanced_search(q, products, synonyms),
    }
    per_query: list[dict[str, Any]] = []
    totals: dict[str, dict[str, list[float]]] = {name: defaultdict(list) for name in engines}
    for item in golden["queries"]:
        row: dict[str, Any] = {
            "q": item["q"],
            "category": item["category"],
            "expect": item["expect"],
        }
        for name, engine in engines.items():
            results = engine(item["q"])
            r = recall_at_k(results, item["expect"], k)
            row[name] = {"top": [int(p["id"]) for p in results[:k]], "recall": r}
            totals[name][item["category"]].append(r)
            totals[name]["overall"].append(r)
        per_query.append(row)

    summary = {
        name: {cat: round(sum(v) / len(v), 3) for cat, v in cats.items()}
        for name, cats in totals.items()
    }
    regressions = [
        cat
        for cat in summary["legacy"]
        if summary["enhanced"].get(cat, 0.0) < summary["legacy"][cat]
    ]
    passed = summary["enhanced"]["overall"] >= threshold and not regressions
    return {
        "slice": slice_name,
        "fixture": str(FIXTURE_DIR.relative_to(ROOT)),
        "k": k,
        "threshold": threshold,
        "passed": passed,
        "regressions": regressions,
        "summary": summary,
        "queries": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slice", default="catalog")
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate(
        _load(args.fixtures / "products.yaml"),
        _load(args.fixtures / "synonyms.yaml"),
        _load(args.fixtures / "golden.yaml"),
        slice_name=args.slice,
    )
    out = args.out or ROOT / "search_eval" / f"{args.slice}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    width = max(len(c) for c in report["summary"]["legacy"])
    print(f"{'category':<{width}}  legacy  enhanced")
    for cat, legacy in report["summary"]["legacy"].items():
        enhanced = report["summary"]["enhanced"][cat]
        flag = "  <-- regression" if cat in report["regressions"] else ""
        print(f"{cat:<{width}}  {legacy:6.3f}  {enhanced:8.3f}{flag}")
    print(f"{'PASS' if report['passed'] else 'FAIL'}: threshold {report['threshold']} -> {out}")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
