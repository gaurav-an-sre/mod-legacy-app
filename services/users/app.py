"""Users slice: login and session establishment."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import pymysql
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI()


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError(f"unsupported MYSQL_DSN scheme: {parsed.scheme!r}")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("MYSQL_DSN must include a database name")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": database,
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


def _db_connect() -> pymysql.connections.Connection:
    dsn = os.environ.get("MYSQL_DSN")
    if not dsn:
        raise RuntimeError("MYSQL_DSN is not set")
    return pymysql.connect(**_parse_mysql_dsn(dsn))


def _request_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_response(value: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(content=value, status_code=status)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/users/login")
async def login(request: Request, response: Response) -> JSONResponse:
    raw = await request.body()
    body = _request_json(raw)
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))

    with _db_connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM users WHERE username = %s AND password = %s",
                (username, password),
            )
            user = cursor.fetchone()

    if not user:
        return _json_response({"error": "invalid credentials"}, 401)

    response.set_cookie(
        key="PHPSESSID",
        value=os.urandom(16).hex(),
        httponly=True,
        path="/",
    )
    return _json_response(
        {
            "ok": True,
            "user": {
                "id": int(user["id"]),
                "username": user["username"],
                "display_name": user["display_name"],
            },
        }
    )
