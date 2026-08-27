"""The promotion gate: the orchestrator measures parity itself, never the agent's claim."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PARITY_THRESHOLD


@dataclass
class ParityGate:
    """Runs `tools/parity.py` and reads the report it wrote."""

    repo: Path
    threshold: float = PARITY_THRESHOLD

    def report_path(self, slice_name: str) -> Path:
        return self.repo / "parity" / f"{slice_name}.json"

    def measure(self, slice_name: str) -> dict[str, Any]:
        subprocess.run(
            [sys.executable, "tools/parity.py", "--slice", slice_name, "--threshold", "1.0"],
            cwd=self.repo,
            check=False,
        )
        path = self.report_path(slice_name)
        if not path.exists():
            return {"slice": slice_name, "match_rate": 0.0, "missing_report": str(path)}
        return json.loads(path.read_text(encoding="utf-8"))

    def rate(self, report: dict[str, Any]) -> float:
        try:
            return float(report.get("match_rate", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def passed(self, report: dict[str, Any]) -> bool:
        return self.rate(report) >= self.threshold
