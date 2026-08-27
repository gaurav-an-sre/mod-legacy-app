"""Migration console for the strangler demo."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()
ROOT = Path(__file__).parents[1]


def _counts(window: int = 500) -> dict[str, dict[str, int]]:
    """Recent legacy/candidate request counts per route from the façade log."""
    counts: dict[str, dict[str, int]] = {}
    path = ROOT / "strangler" / "logs" / "access.log"
    if not path.exists():
        return counts
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-window:]
    for line in lines:
        fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
        route = fields.get("route")
        backend = fields.get("backend")
        if route is None or backend not in ("legacy", "candidate"):
            continue
        counts.setdefault(route, {"legacy": 0, "candidate": 0})[backend] += 1
    return counts


def _report_time(report_path: Path) -> str | None:
    if not report_path.exists():
        return None
    stamp = datetime.fromtimestamp(report_path.stat().st_mtime, tz=UTC)
    return stamp.strftime("%Y-%m-%d %H:%M:%SZ")


def _state() -> list[dict[str, Any]]:
    routes = yaml.safe_load((ROOT / "strangler" / "routes.yaml").read_text(encoding="utf-8"))
    counts = _counts()
    values = []
    for name, config in routes["slices"].items():
        report_path = ROOT / "parity" / f"{name}.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        upstream = config.get("upstream")
        values.append(
            {
                "name": name,
                "weight": int(config.get("weight", 0)) if upstream else 0,
                "upstream": upstream or "legacy only",
                "rate": report.get("match_rate"),
                "timestamp": _report_time(report_path),
                "counts": counts.get(name, {"legacy": 0, "candidate": 0}),
            }
        )
    return values


@app.get("/_migration", response_class=HTMLResponse)
@app.get("/_migration/", response_class=HTMLResponse)
def migration_console() -> str:
    cards = []
    for state in _state():
        rate = state["rate"]
        started = state["upstream"] != "legacy only"
        gate = started and rate is not None and rate >= 0.99
        if not started:
            label, tone = "NOT STARTED", "idle"
        elif gate:
            label, tone = "GATE READY", "good"
        else:
            label, tone = "NEEDS WORK", "bad"
        rate_text = f"{rate:.1%}" if rate is not None else "not run"
        timestamp = html.escape(state["timestamp"]) if state["timestamp"] else "—"
        cards.append(
            f'<section class="card"><div class="row"><h2>{html.escape(state["name"])}</h2>'
            f'<strong class="{tone}">{label}</strong></div>'
            f"<p>Candidate: <code>{html.escape(str(state['upstream']))}</code> · "
            f"parity: <b>{rate_text}</b> · report: {timestamp}</p>"
            f'<div class="bar"><span style="width:{state["weight"]}%"></span></div>'
            f"<p><b>{state['weight']}%</b> candidate traffic · "
            f"recent requests on this slice: legacy {state['counts']['legacy']} · "
            f"candidate {state['counts']['candidate']}</p></section>"
        )
    page = (
        '<!doctype html><html><head><meta http-equiv="refresh" content="3">'
        '<meta name="viewport" content="width=device-width">'
        "<title>Migration Console</title><style>"
        "body{font-family:Inter,Arial,sans-serif;background:#111827;"
        "color:#e5e7eb;margin:0;padding:32px}"
        "main{max-width:900px;margin:auto}.card{background:#1f2937;"
        "border:1px solid #374151;border-radius:10px;padding:20px;margin:16px 0}"
        "h1{font-size:2rem}.row{display:flex;justify-content:space-between;"
        "align-items:center}.good{color:#86efac}.bad{color:#fca5a5}.idle{color:#94a3b8}"
        ".bar{height:14px;border-radius:8px;background:#374151;overflow:hidden}"
        ".bar span{display:block;height:100%;background:#22c55e}"
        "code{color:#93c5fd}p{color:#cbd5e1}"
        "</style></head><body><main><h1>Strangler migration console</h1>"
        "<p>Auto-refreshing façade view · replay parity is the promotion gate.</p>"
    )
    page += "".join(cards)
    page += "</main></body></html>"
    return page
