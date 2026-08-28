"""Contract tests for the extracted catalog service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.catalog.app.main import app, money, product_row

SEED_ROWS = [
    {
        "id": 1,
        "sku": "MUG-BLUE",
        "name": "Blue Coffee Mug",
        "description": "A sturdy blue mug for long debugging sessions.",
        "price_cents": 1299,
        "category": "home",
        "inventory": 42,
    },
    {
        "id": 2,
        "sku": "NOTE-GRID",
        "name": "Grid Notebook",
        "description": "Hardcover notebook with graph paper.",
        "price_cents": 899,
        "category": "office",
        "inventory": 100,
    },
    {
        "id": 3,
        "sku": "CABLE-USB",
        "name": "USB-C Cable",
        "description": "One metre braided charging cable.",
        "price_cents": 1599,
        "category": "electronics",
        "inventory": 25,
    },
    {
        "id": 4,
        "sku": "STICKER-OPS",
        "name": "Ops Sticker Pack",
        "description": "Five durable stickers for laptops and monitors.",
        "price_cents": 499,
        "category": "office",
        "inventory": 200,
    },
]


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_sql = ""
        self.last_params: tuple[Any, ...] = ()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchall(self) -> list[dict[str, Any]]:
        if "LIKE" in self.last_sql:
            like = self.last_params[0].strip("%")
            per_page = int(self.last_params[2])
            offset = int(self.last_params[3])
            matched = [
                row
                for row in self.rows
                if like.lower() in row["name"].lower() or like.lower() in row["category"].lower()
            ]
            return matched[offset : offset + per_page]
        return []

    def fetchone(self) -> dict[str, Any] | None:
        if "WHERE id" in self.last_sql:
            product_id = int(self.last_params[0])
            return next((row for row in self.rows if row["id"] == product_id), None)
        return None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.rows)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    with patch("services.catalog.app.main.connect", return_value=FakeConnection(SEED_ROWS)):
        yield TestClient(app)


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_money_matches_legacy_number_format() -> None:
    assert money(1299) == "12.99"
    assert money(499) == "4.99"


def test_product_row_shape() -> None:
    assert product_row(SEED_ROWS[0]) == {
        "id": 1,
        "sku": "MUG-BLUE",
        "name": "Blue Coffee Mug",
        "description": "A sturdy blue mug for long debugging sessions.",
        "price": "12.99",
        "category": "home",
        "inventory": 42,
    }


def test_list_products_all(client: TestClient) -> None:
    response = client.get("/api/catalog/products", params={"q": "", "page": 1, "per_page": 20})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["per_page"] == 20
    assert len(body["products"]) == 4
    assert body["products"][0]["price"] == "12.99"


def test_list_products_search(client: TestClient) -> None:
    response = client.get("/api/catalog/products", params={"q": "mug", "page": 1, "per_page": 20})
    assert response.status_code == 200
    body = response.json()
    assert len(body["products"]) == 1
    assert body["products"][0]["name"] == "Blue Coffee Mug"


def test_list_products_empty_results(client: TestClient) -> None:
    response = client.get(
        "/api/catalog/products", params={"q": "missing", "page": 1, "per_page": 20}
    )
    assert response.status_code == 200
    assert response.json()["products"] == []


def test_list_products_clamps_page_and_per_page(client: TestClient) -> None:
    response = client.get("/api/catalog/products", params={"page": 0, "per_page": 99})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["per_page"] == 20


def test_get_product_success(client: TestClient) -> None:
    response = client.get("/api/catalog/product", params={"id": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["price"] == "12.99"
    assert "error" not in body


def test_get_product_missing_id(client: TestClient) -> None:
    response = client.get("/api/catalog/product")
    assert response.status_code == 400
    assert response.json() == {"error": "id is required"}


def test_get_product_invalid_id(client: TestClient) -> None:
    for invalid in ("abc", "1.5", "-1", ""):
        response = client.get("/api/catalog/product", params={"id": invalid})
        assert response.status_code == 400
        assert response.json() == {"error": "id is required"}


def test_get_product_unknown_id(client: TestClient) -> None:
    response = client.get("/api/catalog/product", params={"id": 999})
    assert response.status_code == 404
    assert response.json() == {"error": "product not found"}


def test_get_product_leading_zero_id(client: TestClient) -> None:
    response = client.get("/api/catalog/product", params={"id": "01"})
    assert response.status_code == 200
    assert response.json()["id"] == 1
