"""Contract tests for catalog API routes (legacy parity)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app, money, product_row

client = TestClient(app)

SEED_ROW = {
    "id": 1,
    "sku": "MUG-BLUE",
    "name": "Blue Coffee Mug",
    "description": "A sturdy blue mug for long debugging sessions.",
    "price_cents": 1299,
    "category": "home",
    "inventory": 42,
}


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_money_format() -> None:
    assert money(1299) == "12.99"
    assert money(499) == "4.99"


def test_product_row_shape() -> None:
    row = product_row(SEED_ROW)
    assert row == {
        "id": 1,
        "sku": "MUG-BLUE",
        "name": "Blue Coffee Mug",
        "description": "A sturdy blue mug for long debugging sessions.",
        "price": "12.99",
        "category": "home",
        "inventory": 42,
    }


@patch("app.connect_db")
def test_products_list_default(mock_connect: MagicMock) -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [SEED_ROW]
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = conn

    response = client.get("/api/catalog/products", params={"q": "", "page": 1, "per_page": 20})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["per_page"] == 20
    assert len(body["products"]) == 1
    assert body["products"][0]["price"] == "12.99"


@patch("app.connect_db")
def test_products_search_empty(mock_connect: MagicMock) -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = conn

    response = client.get("/api/catalog/products", params={"q": "missing", "page": 1, "per_page": 20})
    assert response.status_code == 200
    assert response.json()["products"] == []


@patch("app.connect_db")
def test_product_detail_ok(mock_connect: MagicMock) -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = SEED_ROW
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = conn

    response = client.get("/api/catalog/product", params={"id": 1})
    assert response.status_code == 200
    assert response.json()["sku"] == "MUG-BLUE"


def test_product_missing_id() -> None:
    response = client.get("/api/catalog/product")
    assert response.status_code == 400
    assert response.json() == {"error": "id is required"}


def test_product_non_digit_id() -> None:
    response = client.get("/api/catalog/product", params={"id": "abc"})
    assert response.status_code == 400
    assert response.json() == {"error": "id is required"}


@patch("app.connect_db")
def test_product_not_found(mock_connect: MagicMock) -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = conn

    response = client.get("/api/catalog/product", params={"id": 999})
    assert response.status_code == 404
    assert response.json() == {"error": "product not found"}


def test_products_per_page_capped_at_20() -> None:
    with patch("app.connect_db") as mock_connect:
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = conn

        response = client.get("/api/catalog/products", params={"per_page": 100})
        assert response.status_code == 200
        assert response.json()["per_page"] == 20


def test_legacy_json_compact() -> None:
    from app import legacy_json

    resp = legacy_json({"page": 1, "per_page": 20, "products": []})
    assert json.loads(resp.body) == {"page": 1, "per_page": 20, "products": []}
