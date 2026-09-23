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

_COLUMN_NOTES = {
    "legacy": "Direct from the PHP monolith",
    "candidate": "Direct from the extracted catalog",
    "facade": "The shelf shoppers see today",
}

_SEARCH_ICON = (
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/>'
    '<path d="M16.2 16.2L21 21" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round"/></svg>'
)

_PAGE_CSS = """
:root{--clay:#9a3412;--chili:#c2410c;--ink:#1c1917;--muted:#78716c;--cream:#fff7ed;
--paper:#f6efe6;--line:#f0dccb}
*{box-sizing:border-box}
body{font-family:Inter,"Noto Sans Thai","Noto Sans",Tahoma,sans-serif;
background:var(--paper);color:var(--ink);margin:0;line-height:1.45}
.mast{background:var(--clay);color:#fff;display:flex;justify-content:space-between;
align-items:center;gap:16px;padding:18px 6%}
.brand{display:flex;gap:12px;align-items:center;color:#fff;text-decoration:none}
.mark{width:44px;height:44px;border-radius:14px;background:#fff;color:var(--clay);
display:grid;place-items:center;font-weight:800;letter-spacing:-.03em}
.brand strong{display:block;font-size:1.3rem;letter-spacing:-.02em}
.brand small{display:block;color:#fed7aa;font-size:.82rem}
.mast nav a{color:#fff;text-decoration:none;border:1px solid rgba(255,255,255,.4);
padding:6px 12px;border-radius:999px;font-size:.85rem}
.awning{height:10px;background:repeating-linear-gradient(90deg,#e8a317 0 18px,#fff7ed 18px 32px)}
main{max-width:1120px;margin:0 auto;padding:8px 20px 56px}
.finder{padding:32px 4px 4px;max-width:760px;margin:0 auto}
.eyebrow{margin:0 0 6px;font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;
color:var(--chili);font-weight:700}
h1{margin:0 0 8px;font-size:2.15rem;line-height:1.15;letter-spacing:-.03em}
.lede{margin:0 0 18px;color:#57534e;font-size:1.02rem}
.searchbar{display:flex;align-items:center;gap:8px;background:#fff;border:2px solid var(--chili);
border-radius:999px;padding:6px 6px 6px 18px;box-shadow:0 12px 32px rgba(154,52,18,.12)}
.query{flex:1;display:flex;min-width:0}
.searchbar input{width:100%;border:0;background:transparent;font:inherit;font-size:1.15rem;
padding:12px 0;outline:none;color:var(--ink)}
.searchbar input::placeholder{color:#a8a29e}
.searchbar:focus-within{box-shadow:0 0 0 4px rgba(194,65,12,.16),0 12px 32px rgba(154,52,18,.12)}
.searchbar button:hover{background:#9a3412}
.searchbar button{display:inline-flex;align-items:center;gap:8px;background:var(--chili);color:#fff;
border:0;border-radius:999px;padding:12px 22px;font:inherit;font-size:1rem;font-weight:700;
cursor:pointer}
.icon{width:18px;height:18px;display:block}
.suggest{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:14px 0 0}
.suggest-label{color:var(--muted);font-size:.85rem}
.chip{display:inline-block;background:#fff;color:var(--clay);border:1px solid #fdba74;
padding:5px 12px;border-radius:999px;text-decoration:none;font-size:.92rem}
.chip:hover{background:#ffedd5}
.echo{margin:16px 0 0;color:#44403c}
.lab{margin-top:28px;background:#fff;border:1px solid var(--line);border-radius:18px;
padding:20px 20px 24px}
.lab-head{display:flex;justify-content:space-between;gap:16px;align-items:center;
margin-bottom:16px}
.lab h2{margin:0 0 4px;font-size:1.35rem}
.lab-head p{margin:0}
.blurb{color:var(--muted);max-width:42rem}
.weight{background:var(--cream);border:1px solid #fdba74;border-radius:999px;padding:8px 14px;
color:var(--clay);white-space:nowrap;font-size:.9rem}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.col{background:#fffaf5;border:1px solid var(--line);border-radius:14px;padding:14px 14px 12px;
display:flex;flex-direction:column;min-width:0}
.col h2{font-size:1rem;margin:0;color:var(--clay)}
.col-top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.note{margin:4px 0 8px;color:var(--muted);font-size:.8rem}
.meta{color:var(--muted);font-size:.85rem;margin:0 0 10px}
.col.facade{background:#fff;border:2px solid var(--chili);box-shadow:0 8px 24px rgba(194,65,12,.08)}
.live{background:var(--chili);color:#fff;border-radius:999px;padding:2px 8px;font-size:.7rem;
font-weight:700;letter-spacing:.04em}
.shelf{display:flex;flex-direction:column;gap:10px}
.product{background:#fff;border:1px solid #f3e4d6;border-radius:12px;padding:12px 12px 10px}
.card-top{display:flex;align-items:center;gap:8px}
.pill{background:#ffedd5;color:var(--clay);border-radius:999px;padding:2px 8px;font-size:.75rem}
.price{margin-left:auto;font-weight:700;color:var(--clay);font-variant-numeric:tabular-nums}
.product h3{margin:8px 0 2px;font-size:1.02rem;line-height:1.35;font-weight:600}
.sku{margin:0;color:var(--muted);font-size:.78rem}
.empty{margin:8px 0;padding:16px 12px;border:1px dashed #e7d3c1;border-radius:12px;
background:rgba(255,255,255,.65)}
.bad{color:#b91c1c}.idle{color:#a8a29e}
.badge{padding:2px 8px;border-radius:999px;font-size:.75rem;color:#fff;background:#78716c}
.badge.candidate{background:#16a34a}.badge.legacy{background:#57534e}
.url{margin-top:auto;padding-top:10px;font-size:.8rem}
code{color:var(--clay)}a{color:var(--chili)}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:860px){
.cols{grid-template-columns:1fr}
.lab-head{flex-direction:column;align-items:flex-start}
.weight{white-space:normal}
.mast{flex-wrap:wrap;padding:16px 20px}
h1{font-size:1.7rem}
.searchbar{border-radius:18px;flex-wrap:wrap;padding:8px}
.searchbar button{width:100%;justify-content:center}
}
"""


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
    category = str(item.get("category") or "").strip()
    price = html.escape(str(item.get("price", "")))
    sku = html.escape(str(item.get("sku", "")))
    pill = f'<span class="pill">{html.escape(category)}</span>' if category else ""
    return (
        '<article class="product">'
        f'<div class="card-top">{pill}<span class="price">฿{price}</span></div>'
        f"<h3>{name}</h3>"
        f'<p class="sku">SKU <code>{sku}</code></p>'
        "</article>"
    )


def _column(column: dict[str, Any], query: str) -> str:
    items = column["items"]
    if column.get("error"):
        body = f'<p class="empty bad">unreachable — {html.escape(column["error"])}</p>'
    elif not query:
        body = '<p class="empty idle">type a query</p>'
    elif not items:
        body = '<p class="empty bad">0 results</p>'
    else:
        body = '<div class="shelf">' + "".join(_product(i) for i in items[:5]) + "</div>"
        if len(items) > 5:
            body += f'<p class="idle">+{len(items) - 5} more</p>'
    served = column.get("served_by")
    badge = (
        f'<span class="badge {html.escape(served)}">served by {html.escape(served)}</span>'
        if served
        else ""
    )
    count = ""
    if query and items and not column.get("error"):
        count = f"{len(items)} result{'s' if len(items) != 1 else ''}"
    meta_bits = " ".join(part for part in (count, badge) if part)
    meta = f'<p class="meta">{meta_bits}</p>' if meta_bits else ""
    note = html.escape(_COLUMN_NOTES.get(column["key"], ""))
    live = '<span class="live">Live</span>' if column["key"] == "facade" else ""
    return (
        f'<section class="col {html.escape(column["key"])}">'
        f'<div class="col-top"><h2>{html.escape(column["title"])}</h2>{live}</div>'
        f'<p class="note">{note}</p>'
        f"{meta}{body}"
        f'<p class="url"><a href="{html.escape(column["url"])}">JSON</a></p></section>'
    )


def render_shop(query: str, columns: list[dict[str, Any]], weight: int | None) -> str:
    chips = "".join(
        f'<a class="chip" href="/_migration/shop?{urlencode({"q": s})}">{html.escape(s)}</a>'
        for s in SUGGESTIONS
    )
    weight_text = f"{weight}%" if weight is not None else "n/a"
    echo = (
        f'<p class="echo">ผลการค้นหา · results for <b>{html.escape(query)}</b></p>' if query else ""
    )
    return (
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Sawan Mart · ค้นหาสินค้า</title><style>"
        f"{_PAGE_CSS}</style></head><body>"
        '<header class="mast"><a class="brand" href="/_migration/shop">'
        '<span class="mark">SM</span><span><strong>Sawan Mart</strong>'
        "<small>ร้านของชำ · neighbourhood grocer</small></span></a>"
        '<nav><a href="/_migration/">Migration console</a></nav></header>'
        '<div class="awning" aria-hidden="true"></div><main>'
        '<section class="finder"><p class="eyebrow">On the shelf</p>'
        '<h1 id="shop-search-label">ค้นหาสินค้า</h1>'
        '<p class="lede">พิมพ์ชื่อสินค้าเป็นไทยหรืออังกฤษ — fish sauce, โค้ก, milk, mugs.</p>'
        '<form class="searchbar" method="get" action="/_migration/shop" role="search">'
        '<label class="query"><span class="sr">ค้นหาสินค้า</span>'
        f'<input type="search" name="q" value="{html.escape(query)}" '
        'placeholder="ค้นหาสินค้า เช่น โค้ก, น้ำปลา, milk" '
        'aria-labelledby="shop-search-label" autofocus></label>'
        f'<button type="submit">{_SEARCH_ICON}ค้นหา</button></form>'
        f'<div class="suggest"><span class="suggest-label">ลองค้นหา</span>{chips}</div>'
        f"{echo}</section>"
        '<section class="lab" aria-label="Migration comparison">'
        '<div class="lab-head"><div><p class="eyebrow">Migration lab</p>'
        "<h2>หนึ่งคำค้น สามชั้นวาง</h2>"
        '<p class="blurb">One search on legacy PHP, the extracted catalog, and the façade '
        "shoppers use today.</p></div>"
        f'<p class="weight">catalog candidate weight: <b>{weight_text}</b></p></div>'
        '<div class="cols">'
        + "".join(_column(c, query) for c in columns)
        + "</div></section></main></body></html>"
    )


def shop_page(query: str, weight: int | None, transport: httpx.BaseTransport | None = None) -> str:
    query = query.strip()
    if query:
        columns = fetch_results(query, transport)
    else:
        columns = [{"key": k, "title": t, "url": "", "items": []} for k, t, _, _ in BACKENDS]
    return render_shop(query, columns, weight)
