"""Contract tests for the users slice, matching legacy behaviour."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app import app
from fastapi.testclient import TestClient

client = TestClient(app)

ALICE = {
    "id": 1,
    "username": "alice",
    "password": "demo",
    "display_name": "Alice Operator",
    "is_admin": 0,
}


def _mock_db(user: dict[str, Any] | None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.return_value = user
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


@patch("app._db_connect")
def test_login_success(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(ALICE)
    response = client.post(
        "/api/users/login",
        json={"username": "alice", "password": "demo"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "user": {
            "id": 1,
            "username": "alice",
            "display_name": "Alice Operator",
        },
    }
    assert "is_admin" not in response.json()["user"]


@patch("app._db_connect")
def test_login_invalid_credentials(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(None)
    response = client.post(
        "/api/users/login",
        json={"username": "nobody", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


@patch("app._db_connect")
def test_login_missing_parameters_default_to_empty(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(None)
    response = client.post("/api/users/login", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


@patch("app._db_connect")
def test_login_empty_body_defaults_to_empty_credentials(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(None)
    response = client.post(
        "/api/users/login",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


@patch("app._db_connect")
def test_login_invalid_json_treated_as_empty_object(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(None)
    response = client.post(
        "/api/users/login",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


@patch("app._db_connect")
def test_login_wrong_password_for_known_user(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db(None)
    response = client.post(
        "/api/users/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("app._db_connect")
def test_login_user_id_is_integer(mock_connect: MagicMock) -> None:
    mock_connect.return_value = _mock_db({**ALICE, "id": "1"})
    response = client.post(
        "/api/users/login",
        json={"username": "alice", "password": "demo"},
    )
    payload = response.json()
    assert isinstance(payload["user"]["id"], int)
    assert payload["user"]["id"] == 1
