"""Catalog slice extracted from the legacy monolith."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Query, Request, Response

from .db import connect

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


def json_response(value: object, status: int = 200) -> Response:
    payload = json.dumps(value, ensure_ascii=False).encode()
    return Response(content=payload, status_code=status, media_type="application/json")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog/products")
def list_products(
    q: str = "",
    page: str = "1",
    per_page: str = "20",
) -> Response:
    query = q.strip()
    page_number = max(1, int(page or 1))
    per_page_number = min(20, max(1, int(per_page or 20)))
    offset = (page_number - 1) * per_page_number
    like = f"%{query}%"
    sql = (
        "SELECT * FROM products "
        "WHERE name LIKE %s OR category LIKE %s "
        "ORDER BY id LIMIT %s OFFSET %s"
    )
    with connect() as db:
        with db.cursor() as cursor:
            cursor.execute(sql, (like, like, per_page_number, offset))
            rows = cursor.fetchall()
    products = [product_row(row) for row in rows]
    return json_response(
        {"page": page_number, "per_page": per_page_number, "products": products}
    )


@app.get("/api/catalog/product")
def get_product(request: Request) -> Response:
    raw_id = request.query_params.get("id")
    if raw_id is None or not str(raw_id).isdigit():
        return json_response({"error": "id is required"}, 400)
    product_id = int(raw_id)
    with connect() as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cursor.fetchone()
    if not row:
        return json_response({"error": "product not found"}, 404)
    return json_response(product_row(row))
