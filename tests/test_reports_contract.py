"""Contract tests for the reports slice candidate service."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import Request, urlopen

import pymysql

from services.reports.app import TOP_PRODUCTS_SQL, Handler, fetch_top_products


class _FakeWriter:
    def __init__(self) -> None:
        self.payload = b""
        self.status: int | None = None
        self.headers: dict[str, str] = {}

    def write(self, data: bytes) -> None:
        self.payload += data


class _FakeHandler(Handler):
    def __init__(self) -> None:
        self.wfile = _FakeWriter()
        self.headers: dict[str, str] = {}

    def send_response(self, code: int) -> None:
        self.wfile.status = code

    def send_header(self, keyword: str, value: str) -> None:
        self.wfile.headers[keyword] = value

    def end_headers(self) -> None:
        return None


def _call_get(handler: _FakeHandler, path: str) -> tuple[int, dict[str, str], bytes]:
    handler.path = path
    handler.do_GET()
    assert handler.wfile.status is not None
    return handler.wfile.status, handler.wfile.headers, handler.wfile.payload


def test_healthz_returns_ok() -> None:
    status, headers, body = _call_get(_FakeHandler(), "/healthz")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body.decode()) == {"ok": True}


def test_top_products_success_shape() -> None:
    handler = _FakeHandler()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {"id": 1, "name": "Blue Coffee Mug", "units": 1},
        {"id": 2, "name": "Grid Notebook", "units": 1},
        {"id": 3, "name": "USB-C Cable", "units": 1},
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    handler.db = mock_conn

    status, headers, body = _call_get(handler, "/api/reports/top-products")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body.decode())
    assert payload == {
        "products": [
            {"id": 1, "name": "Blue Coffee Mug", "units": 1},
            {"id": 2, "name": "Grid Notebook", "units": 1},
            {"id": 3, "name": "USB-C Cable", "units": 1},
        ]
    }
    mock_cursor.execute.assert_called_once_with(TOP_PRODUCTS_SQL)


def test_top_products_empty_results() -> None:
    handler = _FakeHandler()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    handler.db = mock_conn

    status, _, body = _call_get(handler, "/api/reports/top-products")
    assert status == 200
    assert json.loads(body.decode()) == {"products": []}


def test_top_products_casts_numeric_fields_to_int() -> None:
    handler = _FakeHandler()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"id": "7", "name": "Widget", "units": "12"}]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    handler.db = mock_conn

    _, _, body = _call_get(handler, "/api/reports/top-products")
    assert json.loads(body.decode()) == {"products": [{"id": 7, "name": "Widget", "units": 12}]}


def test_top_products_database_unavailable() -> None:
    handler = _FakeHandler()
    with patch("services.reports.app.connect_db", side_effect=pymysql.Error):
        handler.db = None
        status, _, body = _call_get(handler, "/api/reports/top-products")
    assert status == 503
    assert body.decode() == "Database unavailable"


def test_unknown_route_returns_404() -> None:
    status, _, body = _call_get(_FakeHandler(), "/api/reports/missing")
    assert status == 404
    assert json.loads(body.decode()) == {"error": "not found"}


def test_fetch_top_products_preserves_row_order() -> None:
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {"id": 2, "name": "B", "units": 5},
        {"id": 1, "name": "A", "units": 5},
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    rows = fetch_top_products(mock_conn)
    assert rows == [{"id": 2, "name": "B", "units": 5}, {"id": 1, "name": "A", "units": 5}]


def test_top_products_ignores_unknown_query_params() -> None:
    handler = _FakeHandler()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    handler.db = mock_conn
    handler.path = "/api/reports/top-products?unknown=1&limit=5"
    handler.do_GET()
    assert handler.wfile.status == 200
    assert json.loads(handler.wfile.payload.decode()) == {"products": []}


def test_top_products_matches_legacy_when_stack_is_up() -> None:
    try:
        legacy = urlopen(Request("http://legacy/api/reports/top-products"), timeout=2)
        candidate = urlopen(
            Request("http://candidate-reports:8004/api/reports/top-products"),
            timeout=2,
        )
    except URLError:
        return
    legacy_payload = json.loads(legacy.read().decode())
    candidate_payload = json.loads(candidate.read().decode())
    assert legacy.status == candidate.status == 200
    assert candidate_payload == legacy_payload
