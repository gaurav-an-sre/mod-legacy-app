"""Migration console for the strangler demo.

Read-only view over the artifacts the harness already produces:

- ``strangler/routes.yaml``      controller-owned candidate weight per slice
- ``strangler/logs/access.log``  façade request distribution (legacy vs candidate)
- ``out/state.json``             durable slice state written by the orchestrator
                                 (agent id, runtime, branch, PR, runs, billed usage)
- ``out/<slice>/*.jsonl``        every Cursor Agent event the controller streamed
- ``parity/<slice>.json``        replay parity report (the promotion gate)
- ``search_eval/<slice>.json``   deterministic Sawan Mart search evaluation

The console never writes anything; promotion and rollback stay with ``tools/cutover.py``.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()
ROOT = Path(__file__).parents[1]
RECENT_EVENTS = 12
PARITY_THRESHOLD = 0.99


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
        bucket = counts.setdefault(route, {"legacy": 0, "candidate": 0, "errors": 0})
        bucket[backend] += 1
        if fields.get("status", "").startswith("5"):
            bucket["errors"] += 1
    return counts


def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return stamp.strftime("%Y-%m-%d %H:%M:%SZ")


def _event_summary(name: str) -> dict[str, Any]:
    """Compact view of the agent event stream: per-phase counts plus the tail."""
    slice_dir = ROOT / "out" / name
    phases: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    if not slice_dir.is_dir():
        return {"phases": phases, "recent": recent, "total": 0}
    for events_path in sorted(slice_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        kinds: Counter[str] = Counter()
        tools: Counter[str] = Counter()
        tail: list[dict[str, Any]] = []
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = str(event.get("type", "event"))
            kinds[kind] += 1
            entry: dict[str, Any] = {"phase": events_path.stem, "type": kind}
            if kind == "tool_call":
                tool = str(event.get("name") or event.get("tool_name") or "unknown")
                tools[tool] += 1
                args = event.get("args") if isinstance(event.get("args"), dict) else {}
                target = args.get("path") or args.get("command") or args.get("pattern") or ""
                entry.update(tool=tool, status=str(event.get("status") or ""), target=str(target))
            elif kind == "status":
                entry["status"] = str(event.get("status") or "")
            elif kind in ("thinking", "assistant"):
                entry["text"] = str(event.get("text") or "")[:160]
            else:
                continue
            tail.append(entry)
        phases[events_path.stem] = {
            "events": sum(kinds.values()),
            "tool_calls": kinds.get("tool_call", 0),
            "top_tools": tools.most_common(5),
            "updated": _mtime(events_path),
        }
        if tail:
            recent = tail
    total = sum(phase["events"] for phase in phases.values())
    return {"phases": phases, "recent": recent[-RECENT_EVENTS:], "total": total}


def _search_eval(name: str) -> dict[str, Any]:
    report = _read_json(ROOT / "search_eval" / f"{name}.json")
    summary = report.get("summary") or {}
    legacy = summary.get("legacy") or {}
    enhanced = summary.get("enhanced") or {}
    if not legacy or not enhanced:
        return {}
    categories = [c for c in legacy if c != "overall"]
    return {
        "passed": bool(report.get("passed")),
        "threshold": report.get("threshold"),
        "k": report.get("k"),
        "queries": len(report.get("queries") or []),
        "legacy_overall": legacy.get("overall"),
        "enhanced_overall": enhanced.get("overall"),
        "categories": [
            {"name": c, "legacy": legacy.get(c), "enhanced": enhanced.get(c)} for c in categories
        ],
        "timestamp": _mtime(ROOT / "search_eval" / f"{name}.json"),
    }


def _state() -> list[dict[str, Any]]:
    routes = yaml.safe_load((ROOT / "strangler" / "routes.yaml").read_text(encoding="utf-8"))
    durable = _read_json(ROOT / "out" / "state.json").get("slices") or {}
    counts = _counts()
    values = []
    for name, config in routes["slices"].items():
        report_path = ROOT / "parity" / f"{name}.json"
        report = _read_json(report_path)
        upstream = config.get("upstream")
        candidate_backed = bool(upstream and config.get("candidate"))
        slice_state = durable.get(name) if isinstance(durable.get(name), dict) else {}
        values.append(
            {
                "name": name,
                "routes": list(config.get("routes") or []),
                "weight": int(config.get("weight", 0)) if candidate_backed else 0,
                "mirror": bool(config.get("mirror", False)),
                "upstream": upstream if candidate_backed else "legacy only",
                "candidate": config.get("candidate") if candidate_backed else None,
                "rate": report.get("match_rate"),
                "matched": report.get("matched"),
                "total": report.get("total"),
                "timestamp": _mtime(report_path),
                "counts": counts.get(name, {"legacy": 0, "candidate": 0, "errors": 0}),
                "agent": {
                    "id": slice_state.get("agent_id"),
                    "runtime": slice_state.get("runtime"),
                    "phase": slice_state.get("phase"),
                    "status": slice_state.get("status"),
                    "branch": slice_state.get("branch"),
                    "pr_url": slice_state.get("pr_url"),
                    "runs": len(slice_state.get("run_ids") or []),
                    "parity_attempts": slice_state.get("parity_attempts"),
                    "duration_ms": slice_state.get("duration_ms") or 0,
                    "error": slice_state.get("error"),
                },
                "usage": slice_state.get("usage") or {},
                "events": _event_summary(name),
                "search_eval": _search_eval(name),
            }
        )
    return values


def _totals(slices: list[dict[str, Any]]) -> dict[str, Any]:
    cents = sum(float(s["usage"].get("charged_cents", 0)) for s in slices)
    tokens = sum(float(s["usage"].get("total_tokens", 0)) for s in slices)
    return {
        "slices": len(slices),
        "candidates": sum(1 for s in slices if s["candidate"]),
        "gate_ready": sum(
            1 for s in slices if s["rate"] is not None and s["rate"] >= PARITY_THRESHOLD
        ),
        "serving": sum(1 for s in slices if s["weight"] > 0),
        "agents": sum(1 for s in slices if s["agent"]["id"]),
        "agent_events": sum(s["events"]["total"] for s in slices),
        "charged_cents": round(cents, 2),
        "total_tokens": int(tokens),
        "usage_recorded": any(s["usage"] for s in slices),
    }


@app.get("/_migration/api/state")
def migration_state() -> dict[str, Any]:
    slices = _state()
    return {
        "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "totals": _totals(slices),
        "slices": slices,
    }


def _pct(value: Any) -> str:
    return f"{float(value):.0%}" if isinstance(value, (int, float)) else "—"


def _dollars(cents: Any) -> str:
    return f"${float(cents) / 100:.2f}" if isinstance(cents, (int, float)) else "—"


def _kilo(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value / 1_000_000:.2f}M" if value >= 1_000_000 else f"{value / 1_000:.0f}k"


def _agent_card(state: dict[str, Any]) -> str:
    agent = state["agent"]
    usage = state["usage"]
    events = state["events"]
    if not agent["id"]:
        return (
            '<div class="sub"><h3>Cursor Agent</h3>'
            "<p class=idle>No agent has been dispatched for this slice.</p></div>"
        )
    pr = (
        f'<a href="{html.escape(agent["pr_url"])}">{html.escape(agent["pr_url"])}</a>'
        if agent["pr_url"]
        else "—"
    )
    phases = "".join(
        f"<li><code>{html.escape(name)}</code> · {info['events']} events · "
        f"{info['tool_calls']} tool calls · top: "
        + ", ".join(f"{html.escape(t)}×{n}" for t, n in info["top_tools"])
        + "</li>"
        for name, info in events["phases"].items()
        if info["events"]
    )
    tail = "".join(
        "<li>"
        + html.escape(
            f"[{e['phase']}] {e['type']}"
            + (
                f" {e.get('tool', '')} {e.get('status', '')} {e.get('target', '')}"
                if e["type"] == "tool_call"
                else ""
            )
            + (f" {e.get('status', '')}" if e["type"] == "status" else "")
            + (f" “{e.get('text', '')}”" if e["type"] in ("thinking", "assistant") else "")
        )
        + "</li>"
        for e in events["recent"]
    )
    if usage:
        usage_text = (
            f"<b>{_dollars(usage.get('charged_cents'))}</b> billed · "
            f"{_kilo(usage.get('total_tokens'))} tokens "
            f"({_kilo(usage.get('cache_read_tokens'))} cache reads) · "
            f"{int(usage.get('runs', 0))} runs via <code>agent.get_usage()</code>"
        )
    else:
        usage_text = "<span class=idle>usage not recorded for this agent</span>"
    duration = agent["duration_ms"]
    duration_text = f" · {duration / 60000:.1f} min agent time" if duration else ""
    return (
        '<div class="sub"><h3>Cursor Agent</h3>'
        f"<p><code>{html.escape(str(agent['id']))}</code> · runtime "
        f"<b>{html.escape(str(agent['runtime'] or 'unknown'))}</b> · phase "
        f"<b>{html.escape(str(agent['phase']))}</b> / {html.escape(str(agent['status']))} · "
        f"{agent['runs']} runs · parity fix attempts {agent['parity_attempts']}{duration_text}</p>"
        f"<p>branch <code>{html.escape(str(agent['branch'] or '—'))}</code> · PR {pr}</p>"
        f"<p>{usage_text}</p>"
        + (f"<p class=bad>error: {html.escape(str(agent['error']))}</p>" if agent["error"] else "")
        + f"<ul class=phases>{phases}</ul>"
        + (
            f"<details><summary>last {len(events['recent'])} events</summary>"
            f"<ul class=tail>{tail}</ul></details>"
            if tail
            else ""
        )
        + "</div>"
    )


def _search_card(state: dict[str, Any]) -> str:
    ev = state["search_eval"]
    if not ev:
        return ""
    rows = "".join(
        f"<tr><td>{html.escape(c['name'])}</td><td>{_pct(c['legacy'])}</td>"
        f"<td class=good>{_pct(c['enhanced'])}</td></tr>"
        for c in ev["categories"]
    )
    verdict = "PASS" if ev["passed"] else "FAIL"
    tone = "good" if ev["passed"] else "bad"
    return (
        '<div class="sub"><h3>Sawan Mart search eval</h3>'
        f"<p>recall@{ev['k']} on {ev['queries']} golden queries · legacy "
        f"<b>{_pct(ev['legacy_overall'])}</b> → enhanced "
        f"<b class=good>{_pct(ev['enhanced_overall'])}</b> · gate "
        f"<b class={tone}>{verdict}</b> (threshold {_pct(ev['threshold'])}) · "
        f"{html.escape(ev['timestamp'] or '—')}</p>"
        f"<table><tr><th>category</th><th>legacy</th><th>enhanced</th></tr>{rows}</table></div>"
    )


@app.get("/_migration", response_class=HTMLResponse)
@app.get("/_migration/", response_class=HTMLResponse)
def migration_console() -> str:
    slices = _state()
    totals = _totals(slices)
    cards = []
    for state in slices:
        rate = state["rate"]
        started = state["upstream"] != "legacy only"
        gate = started and rate is not None and rate >= PARITY_THRESHOLD
        if not started:
            label, tone = "NOT STARTED", "idle"
        elif gate and state["weight"] == 100:
            label, tone = "CUT OVER", "good"
        elif gate and state["weight"] > 0:
            label, tone = f"RAMPING {state['weight']}%", "good"
        elif gate:
            label, tone = "GATE READY", "good"
        else:
            label, tone = "NEEDS WORK", "bad"
        rate_text = f"{rate:.1%}" if rate is not None else "not run"
        matched = f" ({state['matched']}/{state['total']})" if state["matched"] is not None else ""
        timestamp = html.escape(state["timestamp"]) if state["timestamp"] else "—"
        counts = state["counts"]
        served = counts["legacy"] + counts["candidate"]
        share = f"{counts['candidate'] / served:.0%}" if served else "—"
        routes = ", ".join(f"<code>{html.escape(r)}</code>" for r in state["routes"])
        cards.append(
            f'<section class="card"><div class="row"><h2>{html.escape(state["name"])}</h2>'
            f'<strong class="{tone}">{label}</strong></div>'
            f"<p>routes: {routes}</p>"
            f"<p>Candidate: <code>{html.escape(str(state['upstream']))}</code> · "
            f"parity: <b>{rate_text}</b>{matched} · report: {timestamp}"
            + (" · mirror on" if state["mirror"] else "")
            + "</p>"
            f'<div class="bar"><span style="width:{state["weight"]}%"></span></div>'
            f"<p><b>{state['weight']}%</b> candidate traffic · "
            f"recent requests on this slice: legacy {counts['legacy']} · "
            f"candidate {counts['candidate']} (observed share {share}) · "
            f"5xx {counts['errors']}</p>" + _agent_card(state) + _search_card(state) + "</section>"
        )
    cost = _dollars(totals["charged_cents"]) if totals["usage_recorded"] else "not recorded"
    page = (
        '<!doctype html><html><head><meta http-equiv="refresh" content="3">'
        '<meta name="viewport" content="width=device-width">'
        "<title>Migration Console</title><style>"
        "body{font-family:Inter,Arial,sans-serif;background:#111827;"
        "color:#e5e7eb;margin:0;padding:32px}"
        "main{max-width:960px;margin:auto}.card{background:#1f2937;"
        "border:1px solid #374151;border-radius:10px;padding:20px;margin:16px 0}"
        ".sub{border-top:1px solid #374151;margin-top:14px;padding-top:10px}"
        "h1{font-size:2rem}h3{margin:0 0 6px;font-size:1rem;color:#93c5fd}"
        ".row{display:flex;justify-content:space-between;align-items:center}"
        ".good{color:#86efac}.bad{color:#fca5a5}.idle{color:#94a3b8}"
        ".bar{height:14px;border-radius:8px;background:#374151;overflow:hidden}"
        ".bar span{display:block;height:100%;background:#22c55e}"
        "code{color:#93c5fd}p{color:#cbd5e1;margin:6px 0}a{color:#93c5fd}"
        ".kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}"
        ".kpi{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:12px}"
        ".kpi b{display:block;font-size:1.4rem}.kpi span{color:#94a3b8;font-size:.85rem}"
        "table{border-collapse:collapse;margin-top:6px}td,th{padding:2px 12px 2px 0;"
        "text-align:left;color:#cbd5e1}th{color:#94a3b8;font-weight:normal}"
        "ul{margin:4px 0;padding-left:18px;color:#cbd5e1;font-size:.9rem}"
        ".tail{font-family:ui-monospace,monospace;font-size:.8rem}"
        "summary{cursor:pointer;color:#94a3b8}"
        "</style></head><body><main><h1>Sawan Mart · strangler migration console</h1>"
        "<p>Legacy PHP monolith → Cursor-extracted services. Auto-refreshing façade view · "
        "replay parity is the promotion gate · weights are controller-owned "
        '(<a href="/_migration/api/state">JSON</a>).</p>'
        '<div class="kpis">'
    )
    kpis = [
        (f"{totals['candidates']}/{totals['slices']}", "slices with a candidate"),
        (str(totals["gate_ready"]), f"parity gate ≥ {PARITY_THRESHOLD:.0%}"),
        (str(totals["serving"]), "serving candidate traffic"),
        (str(totals["agents"]), "Cursor Agents dispatched"),
        (str(totals["agent_events"]), "agent events persisted"),
        (cost, "billed agent cost"),
    ]
    page += "".join(
        f"<div class=kpi><b>{html.escape(value)}</b><span>{label}</span></div>"
        for value, label in kpis
    )
    page += "</div>"
    page += "".join(cards)
    page += "</main></body></html>"
    return page
