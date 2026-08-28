"""MySQL connection from MYSQL_DSN."""

from __future__ import annotations

import os
import time
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.connections import Connection


def parse_mysql_dsn(dsn: str) -> dict[str, object]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError(f"unsupported MYSQL_DSN scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("MYSQL_DSN must include a database name")
    return {
        "host": parsed.hostname or "db",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def connect(retries: int = 30, delay: float = 1.0) -> Connection:
    dsn = os.getenv("MYSQL_DSN")
    if not dsn:
        raise RuntimeError("MYSQL_DSN is required")
    params = parse_mysql_dsn(dsn)
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            conn = pymysql.connect(
                host=str(params["host"]),
                port=int(params["port"]),
                user=str(params["user"]),
                password=str(params["password"]),
                database=str(params["database"]),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
            return conn
        except pymysql.Error as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"database unavailable: {last_error}")
