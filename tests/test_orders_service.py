"""Contract tests for the orders slice routes."""

from __future__ import annotations

import json
from base64 import b64encode
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from services.orders.app import app, money, product_row

SESSION_SECRET = "legacy-orders-session"

PRODUCT = {
    "id": 1,
    "sku": "MUG-BLUE",
    "name": "Blue Coffee Mug",
    "description": "A sturdy blue mug for long debugging sessions.",
    "price_cents": 1299,
    "category": "home",
    "inventory": 42,
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def set_session(client: TestClient, data: dict[str, Any]) -> None:
    signer = TimestampSigner(SESSION_SECRET)
    payload = b64encode(json.dumps(data).encode()).decode()
    signed = signer.sign(payload.encode()).decode()
    client.cookies.set("session", signed)


def test_money_matches_legacy_format() -> None:
    assert money(0) == "0.00"
    assert money(1299) == "12.99"
    assert money(2598) == "25.98"


def test_product_row_matches_legacy_shape() -> None:
    row = product_row(PRODUCT)
    assert row == {
        "id": 1,
        "sku": "MUG-BLUE",
        "name": "Blue Coffee Mug",
        "description": "A sturdy blue mug for long debugging sessions.",
        "price": "12.99",
        "category": "home",
        "inventory": 42,
    }


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("services.orders.app.db_connect")
def test_cart_empty_session(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    response = client.get("/api/orders/cart")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": "0.00"}
    cursor.execute.assert_not_called()


@patch("services.orders.app.db_connect")
def test_cart_with_items(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = PRODUCT

    set_session(client, {"cart": {1: 2}})

    response = client.get("/api/orders/cart")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == "25.98"
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 2
    assert body["items"][0]["line_total"] == "25.98"
    assert body["items"][0]["product"]["price"] == "12.99"


@patch("services.orders.app.db_connect")
def test_cart_skips_unknown_products(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = None

    set_session(client, {"cart": {999: 1}})

    response = client.get("/api/orders/cart")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": "0.00"}


@patch("services.orders.app.db_connect")
def test_checkout_success(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = PRODUCT

    response = client.post(
        "/api/orders/checkout",
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == 2000
    assert body["status"] == "accepted"
    assert body["total"] == "25.98"


@patch("services.orders.app.db_connect")
def test_checkout_unknown_product(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = None

    response = client.post(
        "/api/orders/checkout",
        json={"product_id": 999, "quantity": 1},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "product not found"}


@patch("services.orders.app.db_connect")
def test_checkout_missing_product_id(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = None

    response = client.post("/api/orders/checkout", json={})

    assert response.status_code == 404
    assert response.json() == {"error": "product not found"}


@patch("services.orders.app.db_connect")
def test_checkout_quantity_defaults_and_clamps(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = PRODUCT

    default_qty = client.post("/api/orders/checkout", json={"product_id": 1})
    assert default_qty.status_code == 201
    assert default_qty.json()["total"] == "12.99"

    clamped = client.post("/api/orders/checkout", json={"product_id": 1, "quantity": 99})
    assert clamped.status_code == 201
    assert clamped.json()["total"] == "129.90"


@patch("services.orders.app.db_connect")
def test_checkout_invalid_json_body(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = None

    response = client.post(
        "/api/orders/checkout",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "product not found"}


@patch("services.orders.app.db_connect")
def test_checkout_increments_order_id_per_session(
    mock_connect: MagicMock, client: TestClient
) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = PRODUCT

    first = client.post("/api/orders/checkout", json={"product_id": 1, "quantity": 1})
    second = client.post("/api/orders/checkout", json={"product_id": 1, "quantity": 1})

    assert first.json()["order_id"] == 2000
    assert second.json()["order_id"] == 2001


@patch("services.orders.app.db_connect")
def test_checkout_clears_cart(mock_connect: MagicMock, client: TestClient) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = PRODUCT

    set_session(client, {"cart": {1: 3}})

    client.post("/api/orders/checkout", json={"product_id": 1, "quantity": 1})
    cart = client.get("/api/orders/cart")

    assert cart.json() == {"items": [], "total": "0.00"}


def test_routes_yaml_registers_orders_candidate() -> None:
    from pathlib import Path

    import yaml

    routes = yaml.safe_load(Path("strangler/routes.yaml").read_text(encoding="utf-8"))
    orders = routes["slices"]["orders"]
    assert orders["weight"] == 0
    assert orders["upstream"] == "candidate_orders"
    assert orders["candidate"] == "orders:8002"
    assert orders["routes"] == ["/api/orders/cart", "/api/orders/checkout"]
