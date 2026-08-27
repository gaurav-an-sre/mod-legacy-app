"""Reports slice candidate: admin top-products report."""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pymysql
from pymysql.cursors import DictCursor

TOP_PRODUCTS_SQL = """
SELECT p.id, p.name, SUM(oi.quantity) AS units
FROM order_items oi
JOIN products p ON p.id = oi.product_id
GROUP BY p.id, p.name
ORDER BY units DESC, p.id
LIMIT 10
"""


def parse_mysql_dsn(dsn: str) -> dict[str, object]:
    parsed = urlparse(dsn)
    if parsed.scheme != "mysql":
        raise ValueError(f"unsupported DSN scheme: {parsed.scheme!r}")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("MYSQL_DSN must include a database name")
    return {
        "host": parsed.hostname or "db",
        "port": parsed.port or 3306,
        "user": parsed.username or "legacy",
        "password": parsed.password or "legacy",
        "database": database,
    }


def connect_db() -> pymysql.connections.Connection:
    dsn = os.getenv("MYSQL_DSN")
    if dsn:
        params = parse_mysql_dsn(dsn)
    else:
        params = {
            "host": os.getenv("DB_HOST", "db"),
            "port": int(os.getenv("DB_PORT", "3306")),
            "user": os.getenv("DB_USER", "legacy"),
            "password": os.getenv("DB_PASSWORD", "legacy"),
            "database": os.getenv("DB_NAME", "legacy_shop"),
        }
    attempts = 0
    while True:
        try:
            conn = pymysql.connect(
                host=str(params["host"]),
                port=int(params["port"]),
                user=str(params["user"]),
                password=str(params["password"]),
                database=str(params["database"]),
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
            )
            return conn
        except pymysql.Error:
            attempts += 1
            if attempts >= 30:
                raise
            time.sleep(0.25)


def fetch_top_products(conn: pymysql.connections.Connection) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(TOP_PRODUCTS_SQL)
        rows = cursor.fetchall()
    return [
        {"id": int(row["id"]), "name": row["name"], "units": int(row["units"])} for row in rows
    ]


class Handler(BaseHTTPRequestHandler):
    db: pymysql.connections.Connection | None = None

    def send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_text(self, value: str, status: int = 200) -> None:
        payload = value.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self.send_json({"ok": True})
            return
        if path == "/api/reports/top-products":
            try:
                conn = self.db or connect_db()
                self.db = conn
                products = fetch_top_products(conn)
            except pymysql.Error:
                self.send_text("Database unavailable", 503)
                return
            self.send_json({"products": products})
            return
        self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args: object) -> None:
        mirrored = self.headers.get("X-Migration-Mirror") == "1"
        sys.stderr.write(f"reports mirror={int(mirrored)} {fmt % args}\n")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8004"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
