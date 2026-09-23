#!/usr/bin/env python3
"""Deny agent edits to the monolith, the seeded database, and route weights.

Wired as a Cursor ``preToolUse`` hook (Write/Delete tools) and a
``beforeShellExecution`` hook (shell redirects, ``sed -i``, ``rm`` ...).
Both hooks share the permission-decision contract: print JSON on stdout,
exit 0. The same file runs unchanged in the IDE, in local SDK agents and
in Cloud Agents, so the immutability rule in AGENTS.md is enforced by the
harness rather than merely requested of the model.
"""

from __future__ import annotations

import json
import re
import sys

PROTECTED = (
    "legacy/",
    "db/",
    "strangler/routes.yaml",
)

READ_ONLY_SHELL = re.compile(
    r"^\s*(cat|less|head|tail|grep|rg|ls|find|diff|git\s+(diff|log|show|status))\b"
)

MESSAGE = (
    "blocked by .cursor/hooks: legacy/ and db/ are the immutable source system and "
    "parity fixture, and the cutover controller owns strangler/routes.yaml. "
    "Extract into services/ instead."
)


def touches_protected(text: str) -> bool:
    return any(part in text for part in PROTECTED)


def decide(payload: dict) -> dict:
    if "command" in payload and "tool_name" not in payload:
        command = str(payload.get("command", ""))
        if touches_protected(command) and not READ_ONLY_SHELL.match(command):
            return deny()
        return {"permission": "allow"}

    tool_input = payload.get("tool_input", {})
    if touches_protected(json.dumps(tool_input)):
        return deny()
    return {"permission": "allow"}


def deny() -> dict:
    return {"permission": "deny", "user_message": MESSAGE, "agent_message": MESSAGE}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    print(json.dumps(decide(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
