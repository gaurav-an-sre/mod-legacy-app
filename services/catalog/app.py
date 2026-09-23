"""Catalog slice extracted from legacy/index.php (bug-for-bug)."""

from __future__ import annotations

import os
import time
from typing import Any

import pymysql
from fastapi import FastAPI, Request
from starlette.responses import Response

app = FastAPI(docs_url=None, redoc_url=None)


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
                raise
            time.sleep(0.25)


def legacy_json(value: object, status: int = 200) -> Response:
    import json

    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return Response(content=body, status_code=status, media_type="application/json")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog/products")
def catalog_products(
    q: str = "",
    page: int | None = None,
    per_page: int | None = None,
) -> Response:
    query = q.strip()
    page_number = max(1, int(page if page is not None else 1))
    per = min(20, max(1, int(per_page if per_page is not None else 20)))
    offset = (page_number - 1) * per

    conn = connect_db()
    try:
        with conn.cursor() as cur:
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
    if raw_id is None or not str(raw_id).isdigit():
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
