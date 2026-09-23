"""Run event streaming: every event persisted, one readable line per tool call."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from cursor_sdk.errors import AgentBusyError

TERMINAL = {"finished", "error", "cancelled", "expired"}
REATTACH_POLL_SECONDS = 20.0
REATTACH_PROMPT = (
    "Your previous turn finished while the controller was disconnected. Do not run any "
    "tools. Reply with exactly the final JSON object from that turn and nothing else."
)


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


def _consume(run: Any, label: str, events_path: Path, mode: str) -> tuple[str, str, str]:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    assistant = ""
    with events_path.open(mode, encoding="utf-8") as events:
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
    run_id = str(_field(result, "id", "run_id") or _field(run, "id", "run_id") or "")
    status = str(getattr(result, "status", "") or "").lower()
    print(f"[{label}] run {run_id or 'unknown'} stream ended ({status or 'no status'})", flush=True)
    return str(final), run_id, status


def stream_run(
    run: Any,
    label: str,
    events_path: Path,
    *,
    agent: Any = None,
    poll_seconds: float = REATTACH_POLL_SECONDS,
    sleep: Any = time.sleep,
) -> tuple[str, str]:
    """Persist the run's events to JSONL and summarize them live; return (text, run_id).

    Cloud streams can drop while the agent is still working (a long docker build,
    for example). The agent is the durable object, not the socket: when the stream
    ends without a terminal result we wait until the agent accepts a new message
    (it raises `agent_busy` until then) and ask it to restate its final reply.
    """
    text, run_id, status = _consume(run, label, events_path, "w")
    completed = status in TERMINAL or (not status and bool(text))
    if completed or agent is None:
        return text, run_id
    print(f"[{label}] stream dropped mid-run; reattaching to the agent", flush=True)
    while True:
        try:
            follow_up = agent.send(REATTACH_PROMPT)
        except AgentBusyError:
            sleep(poll_seconds)
            continue
        text, _, _ = _consume(follow_up, label, events_path, "a")
        return text, run_id
