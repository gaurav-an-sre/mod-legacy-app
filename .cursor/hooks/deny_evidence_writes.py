#!/usr/bin/env python3
"""Deny local agent writes to the legacy source and deterministic database."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    text = json.dumps(payload)
    protected = ("/legacy/", "/db/", "legacy/", "db/")
    if any(part in text for part in protected):
        print("blocked: legacy/ and db/ are immutable evidence", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
