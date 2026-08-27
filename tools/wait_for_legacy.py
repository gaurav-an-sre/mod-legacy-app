"""Wait until the legacy service responds without a server-side error."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import httpx
import yaml

try:
    from tools.parity import LEGACY_ERROR_MARKERS
except ModuleNotFoundError:
    from parity import LEGACY_ERROR_MARKERS


def wait_for_legacy(
    slice_name: str,
    *,
    requests_path: Path = Path("traffic/requests.yaml"),
    legacy_url: str | None = None,
    timeout: float = 120,
    interval: float = 1,
) -> None:
    traffic = yaml.safe_load(requests_path.read_text(encoding="utf-8"))
    requests = traffic.get(slice_name)
    if not requests:
        raise ValueError(f"unknown slice or empty traffic: {slice_name}")
    path = requests[0]["path"]
    legacy_url = legacy_url or os.getenv("LEGACY_URL", "http://localhost:8080")
    deadline = time.monotonic() + timeout
    last_error = "no response"
    with httpx.Client(timeout=5) as client:
        while True:
            try:
                response = client.get(legacy_url.rstrip("/") + path)
                marker = next(
                    (marker for marker in LEGACY_ERROR_MARKERS if marker in response.text),
                    None,
                )
                if response.status_code < 500 and marker is None:
                    return
                last_error = (
                    f"HTTP {response.status_code}"
                    if marker is None
                    else f"response contains {marker!r}"
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"legacy readiness timed out: {last_error}")
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slice", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        wait_for_legacy(args.slice, timeout=args.timeout)
    except (OSError, TimeoutError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
