"""Promote or roll back a slice in the strangler façade."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

WEIGHTS = [0, 5, 50, 100]


def _load_routes(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _reload(repo: Path) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "strangler", "nginx", "-s", "reload"],
        cwd=repo,
        check=True,
    )


def _error_rates(log_path: Path) -> tuple[float, float]:
    counts = {"legacy": [0, 0], "candidate": [0, 0]}
    if not log_path.exists():
        return 0.0, 0.0
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
        backend = fields.get("backend")
        if backend not in counts:
            continue
        counts[backend][0] += 1
        if int(fields.get("status", "500")) >= 500:
            counts[backend][1] += 1
    return tuple(
        counts[name][1] / counts[name][0] if counts[name][0] else 0.0
        for name in ("legacy", "candidate")
    )


def promote(
    slice_name: str,
    routes_path: Path = Path("strangler/routes.yaml"),
    parity_path: Path | None = None,
    log_path: Path = Path("strangler/logs/access.log"),
    threshold: float = 0.99,
    repo: Path = Path("."),
) -> int:
    config = _load_routes(routes_path)
    if slice_name not in config["slices"]:
        raise SystemExit(f"unknown slice: {slice_name}")
    parity_path = parity_path or Path("parity") / f"{slice_name}.json"
    if not parity_path.exists():
        raise SystemExit(f"refusing promotion: missing parity report {parity_path}")
    report = json.loads(parity_path.read_text(encoding="utf-8"))
    rate = float(report.get("match_rate", 0))
    if rate < threshold:
        raise SystemExit(
            f"refusing promotion: parity match rate {rate:.3f} is below threshold {threshold:.3f}"
        )
    legacy_errors, candidate_errors = _error_rates(log_path)
    if candidate_errors > legacy_errors:
        raise SystemExit(
            "refusing promotion: candidate error rate "
            f"{candidate_errors:.3f} exceeds legacy {legacy_errors:.3f}"
        )
    current = int(config["slices"][slice_name].get("weight", 0))
    next_weights = [weight for weight in WEIGHTS if weight > current]
    if not next_weights:
        raise SystemExit(f"slice {slice_name} is already at 100%")
    config["slices"][slice_name]["weight"] = next_weights[0]
    routes_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    subprocess.run([sys.executable, "strangler/render.py"], cwd=repo, check=True)
    _reload(repo)
    print(f"{slice_name}: weight {current} -> {next_weights[0]}")
    return next_weights[0]


def rollback(
    slice_name: str,
    routes_path: Path = Path("strangler/routes.yaml"),
    repo: Path = Path("."),
) -> None:
    config = _load_routes(routes_path)
    if slice_name not in config["slices"]:
        raise SystemExit(f"unknown slice: {slice_name}")
    config["slices"][slice_name]["weight"] = 0
    routes_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    subprocess.run([sys.executable, "strangler/render.py"], cwd=repo, check=True)
    _reload(repo)
    print(f"{slice_name}: rolled back to weight 0")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--slice", required=True)
    promote_parser.add_argument("--threshold", type=float, default=0.99)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--slice", required=True)
    args = parser.parse_args()
    if args.command == "promote":
        promote(args.slice, threshold=args.threshold)
    else:
        rollback(args.slice)


if __name__ == "__main__":
    main()
