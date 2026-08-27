from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.gate import ParityGate
from orchestrator.migrate import Migration
from orchestrator.sdk import CloudFleet
from orchestrator.state import State
from orchestrator.validation import ContractError, parse_phase_reply


class FakeRun:
    def __init__(self, run_id: str, text: str) -> None:
        self.run_id = run_id
        self.text = text

    def stream(self):
        yield SimpleNamespace(type="status", text="running")
        yield SimpleNamespace(type="assistant", text=self.text)

    def wait(self):
        return SimpleNamespace(result=self.text, run_id=self.run_id)


class FakeAgent:
    def __init__(self, agent_id: str, responses: list[str]) -> None:
        self.agent_id = agent_id
        self.responses = responses
        self.prompts: list[str] = []

    def send(self, prompt: str) -> FakeRun:
        self.prompts.append(prompt)
        return FakeRun(f"run-{len(self.prompts)}", self.responses[len(self.prompts) - 1])


class FakeFleet:
    def __init__(self, agent: FakeAgent) -> None:
        self.agent = agent
        self.created = 0
        self.resumed: list[str] = []

    def create_agent(self, *_args, **_kwargs):
        self.created += 1
        return self.agent

    def resume_agent(self, agent_id: str):
        self.resumed.append(agent_id)
        return self.agent


class FakeGate:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def measure(self, _slice: str, **_kwargs: Any):
        return {"match_rate": 1.0}

    @staticmethod
    def rate(report):
        return report["match_rate"]

    @staticmethod
    def passed(report):
        return report["match_rate"] >= 0.99


def extract_json() -> str:
    return json.dumps(
        {
            "slice": "catalog",
            "service_name": "catalog",
            "container_port": 8001,
            "branch": "devin/catalog",
            "routes": ["/api/catalog/products"],
            "parity_match_rate": 1.0,
            "unresolved_differences": [],
            "notes": [],
        }
    )


def cutover_json() -> str:
    return json.dumps(
        {
            "slice": "catalog",
            "steps": [{"weight": 0, "soak_minutes": 1, "watch": ["status"]}],
            "rollback_triggers": [{"signal": "5xx", "threshold": "1%", "action": "weight 0"}],
            "irreversible_operations": [],
            "residual_risk": "shared database",
        }
    )


def notion_json() -> str:
    return json.dumps({"slice": "catalog", "page_id": "p", "page_url": "u", "created": True})


def test_contracts_are_strict() -> None:
    with pytest.raises(ContractError):
        parse_phase_reply("extract", "catalog", '{"slice": "catalog"}')
    assert parse_phase_reply("extract", "catalog", extract_json())["service_name"] == "catalog"


@pytest.mark.parametrize(
    "branch", ["--upload-pack=evil", "feature/../main", "feature@{1}", "feature/"]
)
def test_contract_rejects_invalid_branch(branch: str) -> None:
    payload = json.loads(extract_json())
    payload["branch"] = branch
    with pytest.raises(ContractError, match="valid branch"):
        parse_phase_reply("extract", "catalog", json.dumps(payload))


def test_restart_resumes_existing_agent(tmp_path: Path) -> None:
    agent = FakeAgent("agent-1", [cutover_json(), notion_json()])
    fleet = FakeFleet(agent)
    state_path = tmp_path / "state.json"
    first_state = State()
    first_state.slice("catalog").agent_id = "agent-1"
    first_state.slice("catalog").phase = "cutover_plan"
    first_state.slice("catalog").status = "running"
    first_state.save(state_path)
    resumed_state = State.load(state_path)
    second = Migration(
        repo=Path(__file__).parents[1],
        fleet=fleet,
        gate=FakeGate(tmp_path),
        state=resumed_state,
        state_path=state_path,
        out_dir=tmp_path / "out",
    )
    second._run_slice("catalog", 1)
    assert fleet.created == 0
    assert fleet.resumed == ["agent-1"]
    assert State.load(state_path).slice("catalog").status == "done"


def test_invalid_reply_is_saved_raw_and_slice_fails(tmp_path: Path) -> None:
    agent = FakeAgent("agent-1", ["not json"])
    fleet = FakeFleet(agent)
    state_path = tmp_path / "state.json"
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=fleet,
        gate=FakeGate(tmp_path),
        state=State(),
        state_path=state_path,
        out_dir=tmp_path / "out",
    )
    assert migration.run(["catalog"]) == 1
    assert (tmp_path / "out/catalog/extract.raw.txt").read_text() == "not json"
    assert State.load(state_path).slice("catalog").status == "failed"


def test_cloud_agent_factory_omits_mcp_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeCloudAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeCloudRepository:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeAgent:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(agent_id="a1")

    fake_sdk = SimpleNamespace(
        Agent=FakeAgent,
        AgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        CloudAgentOptions=FakeCloudAgentOptions,
        CloudRepository=FakeCloudRepository,
    )
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)
    fleet = CloudFleet("https://example.test/repo", "secret")
    fleet.create_agent("catalog", 1)
    assert calls[0]["model"] == "composer-2.5"
    assert "options" not in calls[0]
    assert not hasattr(calls[0]["cloud"], "mcp_servers")


def test_cloud_agent_factory_wires_notion_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeStdio:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeAgent:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs["options"])
            return SimpleNamespace(agent_id="a1")

    fake_sdk = SimpleNamespace(
        Agent=FakeAgent,
        AgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        CloudAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        CloudRepository=lambda **kwargs: SimpleNamespace(**kwargs),
        StdioMcpServerConfig=FakeStdio,
    )
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)
    fleet = CloudFleet("https://example.test/repo", "secret")
    servers = {"notion": FakeStdio(command="npx", args=["-y"], env={"NOTION_TOKEN": "token"})}
    fleet.create_agent("catalog", 1, env_vars={"NOTION_TOKEN": "token"}, mcp_servers=servers)
    options = calls[0]
    assert options.mcp_servers["notion"].env["NOTION_TOKEN"] == "token"
    assert options.cloud.env_vars["NOTION_TOKEN"] == "token"


def test_parity_gate_measures_agent_worktree_and_copies_report(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
    verify_dir = tmp_path / ".verify" / "catalog"

    def runner(args: list[str], *, cwd: Path, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, cwd, _kwargs.get("env")))
        if args[:3] == ["git", "worktree", "add"]:
            verify_dir.mkdir(parents=True)
        if args[:4] == ["docker", "compose", "-p", "verify-catalog"] and "parity" in args:
            report_dir = cwd / "parity"
            report_dir.mkdir(exist_ok=True)
            (report_dir / "catalog.json").write_text(
                json.dumps({"slice": "catalog", "match_rate": 0.75}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    gate = ParityGate(tmp_path, runner=runner)
    report = gate.measure(
        "catalog",
        branch="devin/catalog",
        service_name="catalog",
        container_port=8001,
    )
    commands = [args for args, _cwd, _env in calls]
    assert commands[0] == [
        "git",
        "fetch",
        "origin",
        "refs/heads/devin/catalog:refs/remotes/origin/devin/catalog",
    ]
    assert commands[1][:3] == ["git", "worktree", "add"]
    assert commands[2] == [
        "docker",
        "compose",
        "-p",
        "verify-catalog",
        "up",
        "-d",
        "--build",
        "db",
        "legacy",
        "catalog",
    ]
    assert commands[3][0:7] == [
        "docker",
        "compose",
        "-p",
        "verify-catalog",
        "--profile",
        "tools",
        "run",
    ]
    assert "CANDIDATE_URL=http://catalog:8001" in commands[3]
    assert "--threshold" in commands[3]
    assert commands[3][commands[3].index("--threshold") + 1] == "0.99"
    assert calls[3][2]["HOST_UID"] == str(os.getuid())
    assert calls[3][2]["HOST_GID"] == str(os.getgid())
    assert commands[-1] == ["docker", "compose", "-p", "verify-catalog", "down", "-v"]
    assert report["match_rate"] == 0.75
    assert json.loads(gate.report_path("catalog").read_text())["match_rate"] == 0.75


def test_parity_gate_failed_measurement_returns_zero_rate(tmp_path: Path) -> None:
    def runner(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="compose unavailable")

    gate = ParityGate(tmp_path, runner=runner)
    report = gate.measure(
        "catalog",
        branch="devin/catalog",
        service_name="catalog",
        container_port=8001,
    )
    assert gate.rate(report) == 0.0
    assert not gate.passed(report)
    assert "measurement_error" in report


def test_measurement_error_fails_slice_without_parity_retry(tmp_path: Path) -> None:
    class MeasurementErrorGate(FakeGate):
        def measure(self, _slice: str, **_kwargs: Any):
            return {"match_rate": 0.0, "measurement_error": "permission denied"}

    agent = FakeAgent("agent-1", [extract_json()])
    state_path = tmp_path / "state.json"
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=FakeFleet(agent),
        gate=MeasurementErrorGate(tmp_path),
        state=State(),
        state_path=state_path,
        out_dir=tmp_path / "out",
    )

    assert migration.run(["catalog"]) == 1

    state = State.load(state_path).slice("catalog")
    assert state.status == "failed"
    assert state.phase == "failed"
    assert state.parity_attempts == 0
    assert state.error == "catalog measurement failed: permission denied"


def test_parity_gate_rejects_bad_branch_without_invoking_git(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    gate = ParityGate(tmp_path, runner=runner)
    report = gate.measure(
        "catalog",
        branch="--upload-pack=evil",
        service_name="catalog",
        container_port=8001,
    )
    assert gate.rate(report) == 0.0
    assert "invalid branch name" in report["measurement_error"]
    assert calls == []


def test_happy_path_persists_agent_metadata_and_streams(tmp_path: Path) -> None:
    agent = FakeAgent("agent-1", [extract_json(), cutover_json()])
    fleet = FakeFleet(agent)
    state_path = tmp_path / "state.json"
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=fleet,
        gate=FakeGate(tmp_path),
        state=State(),
        state_path=state_path,
        out_dir=tmp_path / "out",
    )
    assert migration.run(["catalog"]) == 0
    state = State.load(state_path).slice("catalog")
    assert state.branch == "devin/catalog"
    assert state.service_name == "catalog"
    assert state.container_port == 8001
    assert state.status == "done"
    assert len(state.run_ids) == 2
    assert (tmp_path / "out/catalog/extract.jsonl").exists()
    assert (tmp_path / "out/catalog/cutover_plan.json").exists()


def test_sdk_api_errors_are_not_retried_via_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("unauthorized")

    fake_sdk = SimpleNamespace(
        Agent=FakeAgent,
        CloudAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        CloudRepository=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)
    fleet = CloudFleet("https://example.test/repo", "secret")
    with pytest.raises(RuntimeError, match="unauthorized"):
        fleet.create_agent("catalog", 1)
