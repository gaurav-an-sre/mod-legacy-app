"""The comparator gate, measured from the agent's pushed branch."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PARITY_THRESHOLD

Runner = Callable[..., subprocess.CompletedProcess[str]]


class MeasurementFailure(RuntimeError):
    """The agent branch could not be materialized or measured."""


BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _validate_branch(branch: str) -> None:
    if (
        len(branch) > 200
        or not BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or branch.endswith(("/", ".lock"))
    ):
        raise MeasurementFailure("invalid branch name")


@dataclass
class ParityGate:
    """Builds the agent branch in isolation and runs the existing parity harness."""

    repo: Path
    threshold: float = PARITY_THRESHOLD
    runner: Runner = subprocess.run

    def report_path(self, slice_name: str) -> Path:
        return self.repo / "parity" / f"{slice_name}.json"

    def _run(self, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.runner(
            args,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )

    def _verify_worktree(self, slice_name: str, branch: str) -> Path:
        _validate_branch(branch)
        verify_dir = self.repo / ".verify" / slice_name
        verify_dir.parent.mkdir(parents=True, exist_ok=True)
        fetched = self._run(
            [
                "git",
                "fetch",
                "origin",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            ],
            cwd=self.repo,
        )
        if fetched.returncode:
            raise MeasurementFailure(
                f"git fetch failed for {branch}: {fetched.stderr.strip() or fetched.stdout.strip()}"
            )
        ref = f"origin/{branch}"
        if verify_dir.exists():
            refreshed = self._run(
                ["git", "checkout", "--detach", ref],
                cwd=verify_dir,
            )
        else:
            refreshed = self._run(
                ["git", "worktree", "add", "--detach", str(verify_dir), ref],
                cwd=self.repo,
            )
        if refreshed.returncode:
            raise MeasurementFailure(
                f"worktree checkout failed for {branch}: "
                f"{refreshed.stderr.strip() or refreshed.stdout.strip()}"
            )
        return verify_dir

    def measure(
        self,
        slice_name: str,
        branch: str,
        service_name: str,
        container_port: int,
    ) -> dict[str, Any]:
        """Return the report produced in the agent branch, or a failed zero-rate report."""
        verify_dir = self.repo / ".verify" / slice_name
        project = f"verify-{slice_name}"
        try:
            verify_dir = self._verify_worktree(slice_name, branch)
            started = self._run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project,
                    "up",
                    "-d",
                    "--build",
                    "db",
                    "legacy",
                    service_name,
                ],
                cwd=verify_dir,
            )
            if started.returncode:
                raise MeasurementFailure(
                    f"compose build failed: {started.stderr.strip() or started.stdout.strip()}"
                )
            source = verify_dir / "parity" / f"{slice_name}.json"
            source.unlink(missing_ok=True)
            # The subprocess exit code is intentionally ignored: this caller compares the report.
            self._run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project,
                    "--profile",
                    "tools",
                    "run",
                    "--rm",
                    "-e",
                    f"CANDIDATE_URL=http://{service_name}:{container_port}",
                    "parity",
                    "python",
                    "tools/parity.py",
                    "--slice",
                    slice_name,
                    "--threshold",
                    str(self.threshold),
                ],
                cwd=verify_dir,
            )
            if not source.exists():
                raise MeasurementFailure(f"parity report was not produced: {source}")
            report = json.loads(source.read_text(encoding="utf-8"))
            if "match_rate" not in report:
                raise MeasurementFailure(f"parity report has no match_rate: {source}")
            destination = self.report_path(slice_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return report
        except (MeasurementFailure, OSError, json.JSONDecodeError) as exc:
            report = {
                "slice": slice_name,
                "threshold": self.threshold,
                "match_rate": 0.0,
                "measurement_error": str(exc),
            }
            destination = self.report_path(slice_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return report
        finally:
            if verify_dir.exists():
                try:
                    self._run(
                        ["docker", "compose", "-p", project, "down", "-v"],
                        cwd=verify_dir,
                    )
                except OSError:
                    pass

    def rate(self, report: dict[str, Any]) -> float:
        try:
            return float(report.get("match_rate", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def passed(self, report: dict[str, Any]) -> bool:
        return self.rate(report) >= self.threshold
