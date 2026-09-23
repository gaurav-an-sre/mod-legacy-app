"""Contract tests for catalog API routes (legacy parity)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pymysql
from app import DatabaseUnavailable, app, money, product_row
from fastapi.testclient import TestClient

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


@patch("app.pymysql.connect")
def test_healthz_reports_database_ok(mock_connect: MagicMock) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    mock_connect.return_value.close.assert_called_once()


@patch("app.pymysql.connect")
def test_healthz_is_503_when_database_unreachable(mock_connect: MagicMock) -> None:
    mock_connect.side_effect = pymysql.OperationalError(2003, "down")
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


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

    response = client.get(
        "/api/catalog/products", params={"q": "missing", "page": 1, "per_page": 20}
    )
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


def _empty_conn() -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


@patch("app.connect_db")
def test_products_malformed_paging_uses_php_int_cast(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _empty_conn()
    response = client.get("/api/catalog/products", params={"page": "abc", "per_page": ""})
    assert response.status_code == 200
    assert response.json()["page"] == 1
    assert response.json()["per_page"] == 1

    response = client.get("/api/catalog/products", params={"page": "2x", "per_page": "3.7"})
    assert response.json()["page"] == 2
    assert response.json()["per_page"] == 3


def test_product_unicode_digit_id_is_rejected() -> None:
    for bad in ("\u0661", "\u00b2"):
        response = client.get("/api/catalog/product", params={"id": bad})
        assert response.status_code == 400
        assert response.json() == {"error": "id is required"}


@patch("app.connect_db", side_effect=DatabaseUnavailable)
def test_database_outage_returns_legacy_503(_: MagicMock) -> None:
    response = client.get("/api/catalog/products")
    assert response.status_code == 503
    assert response.text == "Database unavailable"


@patch("app.connect_db")
def test_enhanced_mode_ranks_instead_of_like(mock_connect: MagicMock, monkeypatch) -> None:
    import app as catalog_app

    monkeypatch.setattr(catalog_app, "SEARCH_MODE", "enhanced")
    mug = dict(SEED_ROW)
    cable = dict(
        SEED_ROW,
        id=2,
        sku="CABLE-USB",
        name="USB-C Cable",
        category="electronics",
        description="One metre braided cable.",
    )
    cursor = MagicMock()
    cursor.fetchall.return_value = [mug, cable]
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = conn

    response = client.get("/api/catalog/products", params={"q": "MUG"})
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["products"]] == [1]
    assert "LIKE" not in cursor.execute.call_args.args[0]
