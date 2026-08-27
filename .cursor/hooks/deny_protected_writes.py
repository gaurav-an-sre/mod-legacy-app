#!/usr/bin/env python3
"""Deny agent edits to the monolith, the seeded database, and route weights."""

from __future__ import annotations

import json
import sys

PROTECTED = (
    "legacy/",
    "db/",
    "strangler/routes.yaml",
)


def main() -> int:
    payload = json.load(sys.stdin)
    text = json.dumps(payload)
    if any(part in text for part in PROTECTED):
        print(
            "blocked: legacy/ and db/ are immutable, and the cutover controller owns "
            "strangler/routes.yaml",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
