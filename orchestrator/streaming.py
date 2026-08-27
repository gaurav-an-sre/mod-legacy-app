"""Run event streaming: every event persisted, one readable line per tool call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_json"):
        return value.to_json()
    if hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items()}
    return value


def _text(message: Any) -> str:
    direct = getattr(message, "text", "")
    if direct:
        return str(direct)
    content = getattr(getattr(message, "message", None), "content", ())
    return "".join(getattr(block, "text", "") for block in content)


def _field(message: Any, *names: str) -> Any:
    raw = _jsonable(message)
    for name in names:
        value = getattr(message, name, None)
        if value is not None:
            return value
        if isinstance(raw, dict) and raw.get(name) is not None:
            return raw[name]
    return None


def stream_run(run: Any, label: str, events_path: Path) -> tuple[str, str]:
    """Persist the run's events to JSONL and summarize them live; return (text, run_id)."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    assistant = ""
    with events_path.open("w", encoding="utf-8") as events:
        for message in run.stream():
            events.write(json.dumps(_jsonable(message), default=str) + "\n")
            kind = getattr(message, "type", "event")
            text = _text(message)
            if kind == "assistant":
                assistant += text
            elif kind == "tool_call":
                tool = _field(message, "tool_name", "name") or "unknown"
                status = _field(message, "status") or "started"
                print(f"[{label}] tool {tool} ({status})", flush=True)
            elif kind == "status":
                status = _field(message, "status", "value") or text or "unknown"
                print(f"[{label}] status {status}", flush=True)
    result = run.wait()
    final = getattr(result, "result", "") or assistant
    run_id = str(getattr(result, "run_id", "") or getattr(run, "run_id", ""))
    print(f"[{label}] run {run_id or 'unknown'} finished", flush=True)
    return str(final), run_id
