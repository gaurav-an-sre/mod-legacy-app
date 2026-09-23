"""Sawan Mart storefront: one search box, the same query through three paths.

- legacy: the PHP monolith directly (`LIKE '%q%'`, ordered by id)
- candidate: the extracted catalog service directly (enhanced ranker when enabled)
- façade: what a shopper gets today, which follows the controller-owned weight

Read-only: this page issues GET requests and renders the JSON; it never touches routes.
"""

from __future__ import annotations

import html
import os
from typing import Any
from urllib.parse import urlencode

import httpx

PRODUCTS_PATH = "/api/catalog/products"
TIMEOUT_SECONDS = 3.0
SUGGESTIONS = ["นํ้าปลา", "นำปลา", "โค้ก", "น้ำอัดลม", "ไข่", "ขนม", "milk", "ล้างจาน", "mug"]

BACKENDS = (
    ("legacy", "Legacy PHP", "SHOP_LEGACY_URL", "http://localhost:8081"),
    ("candidate", "Candidate (Cursor-extracted)", "SHOP_CANDIDATE_URL", "http://localhost:8001"),
    ("facade", "What shoppers get now (façade)", "SHOP_FACADE_URL", "http://localhost:8080"),
)


def _base_url(env_name: str, default: str) -> str:
    return os.getenv(env_name, default).rstrip("/")


def _public_url(env_name: str, default: str) -> str:
    """Browser-facing base for raw-JSON links; in-network URLs aren't reachable from the host."""
    return os.getenv(f"{env_name.removesuffix('_URL')}_PUBLIC_URL", default).rstrip("/")


def fetch_results(query: str, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """One GET per backend; failures become a column-level error, never a 500."""
    columns = []
    params = urlencode({"q": query, "page": 1, "per_page": 20})
    with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
        for key, title, env_name, default in BACKENDS:
            url = f"{_base_url(env_name, default)}{PRODUCTS_PATH}?{params}"
            public = f"{_public_url(env_name, default)}{PRODUCTS_PATH}?{params}"
            column: dict[str, Any] = {"key": key, "title": title, "url": public, "items": []}
            try:
                response = client.get(url)
                column["status"] = response.status_code
                column["served_by"] = response.headers.get("x-migration-served")
                payload = response.json()
                column["items"] = (
                    list(payload.get("products", [])) if isinstance(payload, dict) else []
                )
            except (httpx.HTTPError, ValueError) as exc:
                column["status"] = None
                column["error"] = f"{type(exc).__name__}: {exc}"
            columns.append(column)
    return columns


def _product(item: dict[str, Any]) -> str:
    name = html.escape(str(item.get("name", "")))
    category = html.escape(str(item.get("category", "")))
    price = html.escape(str(item.get("price", "")))
    sku = html.escape(str(item.get("sku", "")))
    return (
        f"<li><b>{name}</b><span class=meta>{category} · ฿{price} · <code>{sku}</code></span></li>"
    )


def _column(column: dict[str, Any], query: str) -> str:
    items = column["items"]
    if column.get("error"):
        body = f"<p class=bad>unreachable — {html.escape(column['error'])}</p>"
    elif not query:
        body = "<p class=idle>type a query</p>"
    elif not items:
        body = "<p class=bad>0 results</p>"
    else:
        body = "<ol>" + "".join(_product(i) for i in items[:5]) + "</ol>"
        if len(items) > 5:
            body += f"<p class=idle>+{len(items) - 5} more</p>"
    served = column.get("served_by")
    badge = (
        f'<span class="badge {html.escape(served)}">served by {html.escape(served)}</span>'
        if served
        else ""
    )
    count = f"{len(items)} result{'s' if len(items) != 1 else ''}" if query else ""
    return (
        f'<section class="col {column["key"]}"><h2>{html.escape(column["title"])}</h2>'
        f"<p class=meta>{count} {badge}</p>{body}"
        f'<p class=url><a href="{html.escape(column["url"])}">JSON</a></p></section>'
    )


def render_shop(query: str, columns: list[dict[str, Any]], weight: int | None) -> str:
    chips = "".join(
        f'<a class=chip href="/_migration/shop?{urlencode({"q": s})}">{html.escape(s)}</a>'
        for s in SUGGESTIONS
    )
    weight_text = f"{weight}%" if weight is not None else "n/a"
    return (
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        "<title>Sawan Mart · search</title><style>"
        "body{font-family:Inter,'Noto Sans Thai',Arial,sans-serif;background:#fff7ed;"
        "color:#1c1917;margin:0}"
        "header{background:#c2410c;color:#fff;padding:16px 6%;display:flex;gap:20px;"
        "align-items:center}header a{color:#fed7aa;text-decoration:none;font-size:.9rem}"
        "main{max-width:1200px;margin:24px auto;padding:0 20px}"
        "form{display:flex;gap:8px}input{flex:1;font-size:1.2rem;padding:12px;"
        "border:2px solid #fdba74;border-radius:8px}button{background:#c2410c;color:#fff;"
        "border:0;padding:12px 20px;border-radius:8px;font-size:1rem}"
        ".chips{margin:10px 0 20px}.chip{display:inline-block;background:#ffedd5;"
        "color:#9a3412;padding:4px 10px;border-radius:999px;margin:2px;text-decoration:none}"
        ".cols{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}"
        ".col{background:#fff;border:1px solid #fed7aa;border-radius:10px;padding:16px}"
        ".col h2{font-size:1rem;margin:0 0 6px;color:#9a3412}.col.facade{border-color:#c2410c;"
        "border-width:2px}ol{padding-left:20px;margin:8px 0}li{margin:8px 0}"
        "li b{display:block}.meta{color:#78716c;font-size:.85rem}.url{font-size:.8rem}"
        ".bad{color:#b91c1c}.idle{color:#a8a29e}.badge{padding:2px 8px;border-radius:999px;"
        "font-size:.75rem;color:#fff;background:#78716c}.badge.candidate{background:#16a34a}"
        "code{color:#9a3412}a{color:#c2410c}"
        "</style></head><body><header><strong>Sawan Mart</strong>"
        '<a href="/_migration/">migration console</a>'
        f"<span style='margin-left:auto'>catalog candidate weight: <b>{weight_text}</b></span>"
        '</header><main><form method=get action="/_migration/shop">'
        f'<input name=q value="{html.escape(query)}" placeholder="ค้นหาสินค้า · search products" '
        "autofocus><button>ค้นหา</button></form>"
        f"<div class=chips>{chips}</div><div class=cols>"
        + "".join(_column(c, query) for c in columns)
        + "</div></main></body></html>"
    )


def shop_page(query: str, weight: int | None, transport: httpx.BaseTransport | None = None) -> str:
    query = query.strip()
    if query:
        columns = fetch_results(query, transport)
    else:
        columns = [{"key": k, "title": t, "url": "", "items": []} for k, t, _, _ in BACKENDS]
    return render_shop(query, columns, weight)
