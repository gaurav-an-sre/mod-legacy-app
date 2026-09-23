"""Emit SQL that adds the Sawan Mart fixture catalog to the live legacy_shop database.

`db/` is immutable, so the Thai products live in `search_eval/sawan_mart/products.yaml`
and are appended at runtime with `make seed-sawan`. Ids are offset so the monolith's own
seed rows (and the parity fixtures that reference them) are untouched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = ROOT / "search_eval" / "sawan_mart" / "products.yaml"
ID_OFFSET = 100
DEFAULT_INVENTORY = 50


def _sql_str(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def render_sql(products: list[dict[str, Any]], id_offset: int = ID_OFFSET) -> str:
    rows = [
        "({id}, {sku}, {name}, {description}, {price}, {category}, {inventory})".format(
            id=int(p["id"]) + id_offset,
            sku=_sql_str(p["sku"]),
            name=_sql_str(p["name"]),
            description=_sql_str(p.get("description", "")),
            price=int(p["price_cents"]),
            category=_sql_str(p["category"]),
            inventory=DEFAULT_INVENTORY,
        )
        for p in products
    ]
    return (
        "SET NAMES utf8mb4;\n"
        "INSERT INTO products "
        "(id, sku, name, description, price_cents, category, inventory) VALUES\n"
        + ",\n".join(rows)
        + "\nON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), "
        "price_cents=VALUES(price_cents), category=VALUES(category);\n"
    )


def load_products(path: Path = PRODUCTS) -> list[dict[str, Any]]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8")) or [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=PRODUCTS)
    parser.add_argument("--id-offset", type=int, default=ID_OFFSET)
    args = parser.parse_args()
    sys.stdout.write(render_sql(load_products(args.products), args.id_offset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
