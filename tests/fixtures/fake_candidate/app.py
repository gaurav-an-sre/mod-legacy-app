"""Tiny candidate service used to demonstrate parity and promotion gates."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PRODUCTS = [
    {
        "id": 1,
        "sku": "MUG-BLUE",
        "name": "Blue Coffee Mug",
        "description": "A sturdy blue mug for long debugging sessions.",
        "price": "12.99",
        "category": "home",
        "inventory": 42,
    },
    {
        "id": 2,
        "sku": "NOTE-GRID",
        "name": "Grid Notebook",
        "description": "Hardcover notebook with graph paper.",
        "price": "8.99",
        "category": "office",
        "inventory": 100,
    },
    {
        "id": 3,
        "sku": "CABLE-USB",
        "name": "USB-C Cable",
        "description": "One metre braided charging cable.",
        "price": "15.99",
        "category": "electronics",
        "inventory": 25,
    },
    {
        "id": 4,
        "sku": "STICKER-OPS",
        "name": "Ops Sticker Pack",
        "description": "Five durable stickers for laptops and monitors.",
        "price": "4.99",
        "category": "office",
        "inventory": 200,
    },
]


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        diverge = os.getenv("FAKE_DIVERGE", "1") == "1"
        if parsed.path == "/api/catalog/products":
            q = query.get("q", [""])[0].lower()
            products = [
                product
                for product in PRODUCTS
                if q in product["name"].lower() or q in product["category"].lower()
            ]
            values = [dict(product) for product in products]
            if diverge and values:
                values[0]["price"] = "13.00"
            self.send_json(
                {
                    "page": int(query.get("page", ["1"])[0]),
                    "per_page": int(query.get("per_page", ["20"])[0]),
                    "products": values,
                }
            )
            return
        if parsed.path == "/api/catalog/product":
            raw_id = query.get("id", [None])[0]
            if raw_id is None:
                self.send_json({"error": "id is required"}, 400)
                return
            product = next((item for item in PRODUCTS if item["id"] == int(raw_id)), None)
            if product is None:
                self.send_json({"error": "product not found"}, 404)
                return
            value = dict(product)
            if diverge:
                value["price"] = "13.00"
            self.send_json(value)
            return
        self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args: object) -> None:
        mirrored = self.headers.get("X-Migration-Mirror") == "1"
        sys.stderr.write(f"candidate mirror={int(mirrored)} {fmt % args}\n")


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
