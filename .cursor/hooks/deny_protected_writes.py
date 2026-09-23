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

PROTECTED_DIRS = ("legacy", "db")

# A command is read-only only if it is a single simple command whose verb cannot
# write (so no `find -delete`, no `sed -i`): any pipe, redirect, separator or
# substitution disqualifies the whole line.
READ_ONLY_SHELL = re.compile(
    r"^\s*(cat|less|head|tail|grep|rg|ls|diff|git\s+(diff|log|show|status))\b"
)
SHELL_COMPOUND = re.compile(r"[|;&<>`\n]|\$\(")
CD_INTO_PROTECTED = re.compile(r"\bcd\s+(?:\S*/)?(legacy|db)(?:/|\s|$)")

MESSAGE = (
    "blocked by .cursor/hooks: legacy/ and db/ are the immutable source system and "
    "parity fixture, and the cutover controller owns strangler/routes.yaml. "
    "Extract into services/ instead."
)


def touches_protected(text: str) -> bool:
    return any(part in text for part in PROTECTED)


def is_read_only(command: str) -> bool:
    return bool(READ_ONLY_SHELL.match(command)) and not SHELL_COMPOUND.search(command)


def cwd_is_protected(payload: dict) -> bool:
    cwd = str(payload.get("cwd", "")).rstrip("/")
    if not cwd:
        return False
    for root in payload.get("workspace_roots", []) or []:
        root = str(root).rstrip("/")
        if cwd == root:
            return False
        if cwd.startswith(root + "/"):
            cwd = cwd[len(root) + 1 :]
            break
    return any(part in PROTECTED_DIRS for part in cwd.split("/"))


def decide(payload: dict) -> dict:
    if "command" in payload and "tool_name" not in payload:
        command = str(payload.get("command", ""))
        if is_read_only(command):
            return {"permission": "allow"}
        if touches_protected(command) or CD_INTO_PROTECTED.search(command):
            return deny()
        if cwd_is_protected(payload):
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
