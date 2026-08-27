"""Orders slice candidate: cart contents and checkout."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "legacy-orders-session"),
)


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


def parse_dsn() -> dict[str, Any]:
    dsn = os.getenv("MYSQL_DSN", "mysql://legacy:legacy@db:3306/legacy_shop")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError(f"unsupported MYSQL_DSN scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/") or "legacy_shop"
    return {
        "host": parsed.hostname or "db",
        "port": parsed.port or 3306,
        "user": parsed.username or "legacy",
        "password": parsed.password or "legacy",
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


def db_connect() -> pymysql.connections.Connection:
    attempts = 0
    while True:
        try:
            return pymysql.connect(**parse_dsn())
        except pymysql.Error:
            attempts += 1
            if attempts >= 30:
                raise


def json_response(value: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        content=value,
        status_code=status,
        media_type="application/json",
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/orders/cart")
def cart(request: Request) -> JSONResponse:
    cart_items = request.session.get("cart", {})
    items: list[dict[str, Any]] = []
    total = 0
    with db_connect() as connection:
        with connection.cursor() as cursor:
            for product_id, quantity in cart_items.items():
                cursor.execute("SELECT * FROM products WHERE id = %s", (int(product_id),))
                row = cursor.fetchone()
                if row:
                    line_total = int(row["price_cents"]) * int(quantity)
                    total += line_total
                    items.append(
                        {
                            "product": product_row(row),
                            "quantity": int(quantity),
                            "line_total": money(line_total),
                        }
                    )
    return json_response({"items": items, "total": money(total)})


@app.post("/api/orders/checkout")
async def checkout(request: Request) -> Response:
    raw = await request.body()
    try:
        parsed = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    product_id = int(parsed.get("product_id", 0) or 0)
    quantity = max(1, min(10, int(parsed.get("quantity", 1) or 1)))
    with db_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cursor.fetchone()
    if not row:
        return json_response({"error": "product not found"}, 404)
    checkout_ids = request.session.get("checkout_ids", [])
    order_id = 2000 + len(checkout_ids)
    checkout_ids.append(order_id)
    request.session["checkout_ids"] = checkout_ids
    request.session["cart"] = {}
    total = int(row["price_cents"]) * quantity
    return json_response(
        {"order_id": order_id, "status": "accepted", "total": money(total)},
        201,
    )
