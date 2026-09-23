"""Catalog slice extracted from legacy/index.php (bug-for-bug)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import pymysql
import yaml
from fastapi import FastAPI, Request
from search import enhanced_search
from starlette.responses import PlainTextResponse, Response

app = FastAPI(docs_url=None, redoc_url=None)

# legacy: bug-for-bug LIKE '%q%' (what the parity gate measures).
# enhanced: Thai-aware ranked search (what search_eval measures); off until promoted.
SEARCH_MODE = os.getenv("SEARCH_MODE", "legacy")
SYNONYMS_PATH = os.getenv("SEARCH_SYNONYMS", "")
# Enrichment layer (sku -> tags) the ranker may use; the products table has no tags column.
TAGS_PATH = os.getenv("SEARCH_TAGS", "")

LEADING_INT = re.compile(r"^\s*[+-]?\d+")


class DatabaseUnavailable(Exception):
    pass


@app.exception_handler(DatabaseUnavailable)
def database_unavailable(_: Request, __: DatabaseUnavailable) -> Response:
    return PlainTextResponse("Database unavailable", status_code=503)


def php_int(value: str | None, default: int) -> int:
    """PHP `(int)` cast: leading integer prefix or 0, never a validation error."""
    if value is None:
        return default
    match = LEADING_INT.match(value)
    return int(match.group(0)) if match else 0


def money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def product_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "sku": row["sku"],
        "name": row["name"],
        "description": row["description"],
        "price": money(int(row["price_cents"])),
        "category": row["category"],
        "inventory": int(row["inventory"]),
    }


def connect_db() -> pymysql.connections.Connection:
    attempts = 0
    while True:
        try:
            conn = pymysql.connect(
                host=os.getenv("DB_HOST", "db"),
                user=os.getenv("DB_USER", "legacy"),
                password=os.getenv("DB_PASSWORD", "legacy"),
                database=os.getenv("DB_NAME", "legacy_shop"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
            return conn
        except pymysql.MySQLError:
            attempts += 1
            if attempts >= 30:
                raise DatabaseUnavailable from None
            time.sleep(0.25)


def load_synonyms() -> dict[str, list[str]]:
    if not SYNONYMS_PATH or not os.path.exists(SYNONYMS_PATH):
        return {}
    with open(SYNONYMS_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_tags() -> dict[str, list[str]]:
    if not TAGS_PATH or not os.path.exists(TAGS_PATH):
        return {}
    with open(TAGS_PATH, encoding="utf-8") as handle:
        rows = yaml.safe_load(handle) or []
    return {str(row["sku"]): list(row.get("tags", [])) for row in rows}


def enrich(rows: list[dict[str, Any]], tags_by_sku: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [{**row, "tags": tags_by_sku.get(str(row.get("sku")), [])} for row in rows]


def legacy_json(value: object, status: int = 200) -> Response:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return Response(content=body, status_code=status, media_type="application/json")


@app.get("/healthz")
def healthz() -> Response:
    """Readiness: a candidate that cannot reach MySQL must not look promotable."""
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "db"),
            user=os.getenv("DB_USER", "legacy"),
            password=os.getenv("DB_PASSWORD", "legacy"),
            database=os.getenv("DB_NAME", "legacy_shop"),
            connect_timeout=2,
        )
    except pymysql.MySQLError:
        return legacy_json({"status": "degraded", "database": "unavailable"}, 503)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        conn.close()
    return legacy_json({"status": "ok", "database": "ok"})


@app.get("/api/catalog/products")
def catalog_products(request: Request) -> Response:
    params = request.query_params
    query = params.get("q", "").strip()
    page_number = max(1, php_int(params.get("page"), 1))
    per = min(20, max(1, php_int(params.get("per_page"), 20)))
    offset = (page_number - 1) * per

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            if SEARCH_MODE == "enhanced" and query:
                cur.execute("SELECT * FROM products ORDER BY id")
                ranked = enhanced_search(
                    query, enrich(cur.fetchall(), load_tags()), load_synonyms()
                )
                rows = ranked[offset : offset + per]
            else:
                sql = (
                    "SELECT * FROM products WHERE name LIKE %s OR category LIKE %s "
                    "ORDER BY id LIMIT %s OFFSET %s"
                )
                pattern = f"%{query}%"
                cur.execute(sql, (pattern, pattern, per, offset))
                rows = cur.fetchall()
    finally:
        conn.close()

    products = [product_row(row) for row in rows]
    return legacy_json({"page": page_number, "per_page": per, "products": products})


@app.get("/api/catalog/product")
def catalog_product(request: Request) -> Response:
    raw_id = request.query_params.get("id")
    if raw_id is None or not raw_id.isascii() or not raw_id.isdigit():
        return legacy_json({"error": "id is required"}, 400)
    product_id = int(raw_id)

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return legacy_json({"error": "product not found"}, 404)
    return legacy_json(product_row(row))
