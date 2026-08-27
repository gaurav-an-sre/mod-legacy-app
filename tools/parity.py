"""Replay recorded traffic and compare legacy responses with a candidate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx
import yaml


def _normalize(value: Any, ignored: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(item, ignored)
            for key, item in sorted(value.items())
            if key not in ignored
        }
    if isinstance(value, list):
        return [_normalize(item, ignored) for item in value]
    return value


def _capture(
    client: httpx.Client, base: str, request: dict[str, Any], ignored: set[str]
) -> tuple[dict[str, Any], Any]:
    """Replay one request and return its record plus the value used for comparison."""
    response = client.request(
        request["method"],
        base.rstrip("/") + request["path"],
        params=request.get("query") or {},
        json=request.get("body"),
        headers=request.get("headers") or {},
    )
    parsed: Any = None
    if "application/json" in response.headers.get("content-type", ""):
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
    record = {"status": response.status_code, "body": response.text, "json": parsed}
    comparable = _normalize(parsed, ignored) if parsed is not None else response.text
    return record, comparable


def compare_slice(
    slice_name: str,
    requests_path: Path = Path("traffic/requests.yaml"),
    normalize_path: Path = Path("traffic/normalize.yaml"),
    legacy_url: str | None = None,
    candidate_url: str | None = None,
    threshold: float = 0.99,
) -> dict[str, Any]:
    traffic = yaml.safe_load(requests_path.read_text(encoding="utf-8"))
    rules = yaml.safe_load(normalize_path.read_text(encoding="utf-8"))
    ignored = set(rules.get("ignore_json_keys", []))
    requests = traffic.get(slice_name)
    if requests is None:
        raise ValueError(f"unknown slice: {slice_name}")
    legacy_url = legacy_url or os.getenv("LEGACY_URL", "http://localhost:8080")
    candidate_url = candidate_url or os.getenv("CANDIDATE_URL", "http://localhost:8081")
    results = []
    matches = 0
    with httpx.Client(timeout=10) as client:
        for request in requests:
            try:
                legacy, legacy_body = _capture(client, legacy_url, request, ignored)
                candidate, candidate_body = _capture(client, candidate_url, request, ignored)
                diff = []
                if legacy["status"] != candidate["status"]:
                    diff.append(
                        f"status differs: legacy={legacy['status']} candidate={candidate['status']}"
                    )
                if legacy_body != candidate_body:
                    diff.append(
                        "body differs: "
                        + json.dumps(
                            {"legacy": legacy_body, "candidate": candidate_body}, sort_keys=True
                        )
                    )
                match = not diff
                if match:
                    matches += 1
                results.append(
                    {"request": request, "legacy": legacy, "candidate": candidate, "diff": diff}
                )
            except Exception as exc:
                results.append(
                    {
                        "request": request,
                        "legacy": None,
                        "candidate": None,
                        "diff": [f"request failed: {exc}"],
                    }
                )
    report = {
        "slice": slice_name,
        "threshold": threshold,
        "match_rate": matches / len(requests) if requests else 1.0,
        "matched": matches,
        "total": len(requests),
        "requests": results,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slice", required=True)
    parser.add_argument("--threshold", type=float, default=0.99)
    parser.add_argument("--legacy-url")
    parser.add_argument("--candidate-url")
    args = parser.parse_args()
    try:
        report = compare_slice(
            args.slice,
            legacy_url=args.legacy_url,
            candidate_url=args.candidate_url,
            threshold=args.threshold,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = Path("parity")
    output.mkdir(exist_ok=True)
    report_path = output / f"{args.slice}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(report_path)
    if report["match_rate"] < report["threshold"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
