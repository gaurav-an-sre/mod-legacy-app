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
from orchestrator.notion import ApiStatusWriter
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


def test_notion_create_page_uses_title_property_shape() -> None:
    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"id": "page-1"}

    class FakeClient:
        def __init__(self) -> None:
            self.json: dict[str, Any] | None = None

        def post(self, _url: str, **kwargs: Any) -> FakeResponse:
            self.json = kwargs["json"]
            return FakeResponse()

    client = FakeClient()
    writer = ApiStatusWriter(token="token", parent_page_id="parent", client=client)

    assert writer._create_page("catalog") == "page-1"
    assert client.json is not None
    assert client.json["properties"] == {
        "title": {"title": [{"type": "text", "text": {"content": "catalog migration status"}}]}
    }


def test_notion_error_includes_response_body() -> None:
    class FakeResponse:
        status_code = 400
        request = SimpleNamespace(method="POST", url="https://api.notion.com/v1/pages")
        text = "title property is invalid"

    class FakeClient:
        def post(self, _url: str, **_kwargs: Any) -> FakeResponse:
            return FakeResponse()

    writer = ApiStatusWriter(token="token", parent_page_id="parent", client=FakeClient())

    with pytest.raises(RuntimeError, match="title property is invalid"):
        writer._create_page("catalog")


def test_notion_record_reuses_page_after_patch_failure() -> None:
    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"id": "page-1"}

    class FakeClient:
        def __init__(self) -> None:
            self.post_count = 0
            self.patch_urls: list[str] = []

        def post(self, _url: str, **_kwargs: Any) -> FakeResponse:
            self.post_count += 1
            return FakeResponse()

        def patch(self, url: str, **_kwargs: Any) -> FakeResponse:
            self.patch_urls.append(url)
            if len(self.patch_urls) == 1:
                raise RuntimeError("transient patch failure")
            return FakeResponse()

    client = FakeClient()
    writer = ApiStatusWriter(token="token", parent_page_id="parent", client=client)
    summary = {"phase": "extract", "status": "running", "weight": 0, "parity_rate": None}

    with pytest.raises(RuntimeError, match="transient patch failure"):
        writer.record("catalog", summary, None)
    assert writer.record("catalog", summary, None) == "page-1"
    assert client.post_count == 1
    assert client.patch_urls == [
        "https://api.notion.com/v1/blocks/page-1/children",
        "https://api.notion.com/v1/blocks/page-1/children",
    ]


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


def test_missing_agent_is_recreated_from_slice_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cursor_sdk import AgentNotFoundError

    class DeadAgent:
        agent_id = "agent-dead"

        def send(self, _prompt: str):
            raise AgentNotFoundError("agent gone", code="agent_not_found")

    class FreshAgent(FakeAgent):
        def __init__(self) -> None:
            super().__init__("agent-fresh", [cutover_json()])

    class RecreatingFleet:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        def resume_agent(self, _agent_id: str) -> DeadAgent:
            return DeadAgent()

        def create_agent(self, *args: Any, **kwargs: Any) -> FreshAgent:
            self.created.append({"args": args, "kwargs": kwargs})
            return FreshAgent()

    fleet = RecreatingFleet()
    state = State()
    state.slice("catalog").agent_id = "agent-dead"
    state.slice("catalog").branch = "cursor/catalog"
    state.slice("catalog").phase = "cutover_plan"
    state.slice("catalog").status = "running"
    state_path = tmp_path / "state.json"
    state.save(state_path)
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=fleet,
        gate=FakeGate(tmp_path),
        state=state,
        state_path=state_path,
        out_dir=tmp_path / "out",
    )

    assert migration.run(["catalog"]) == 0

    saved = State.load(state_path).slice("catalog")
    assert saved.agent_id == "agent-fresh"
    assert len(fleet.created) == 1
    assert fleet.created[0]["kwargs"]["starting_ref"] == "cursor/catalog"
    assert "agent agent-dead is gone; recreated as agent-fresh" in capsys.readouterr().out


def test_second_missing_agent_fails_slice_after_one_recreation(tmp_path: Path) -> None:
    from cursor_sdk import AgentNotFoundError

    class DeadAgent:
        agent_id = "agent-dead"

        def send(self, _prompt: str):
            raise AgentNotFoundError("agent gone", code="agent_not_found")

    class RecreatingFleet:
        def __init__(self) -> None:
            self.created = 0

        def resume_agent(self, _agent_id: str) -> DeadAgent:
            return DeadAgent()

        def create_agent(self, *_args: Any, **_kwargs: Any) -> DeadAgent:
            self.created += 1
            return DeadAgent()

    fleet = RecreatingFleet()
    state = State()
    state.slice("catalog").agent_id = "agent-dead"
    state.slice("catalog").branch = "cursor/catalog"
    state.slice("catalog").phase = "cutover_plan"
    state.slice("catalog").status = "running"
    state_path = tmp_path / "state.json"
    state.save(state_path)
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=fleet,
        gate=FakeGate(tmp_path),
        state=state,
        state_path=state_path,
        out_dir=tmp_path / "out",
    )

    assert migration.run(["catalog"]) == 1

    saved = State.load(state_path).slice("catalog")
    assert saved.status == "failed"
    assert fleet.created == 1


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
    fleet.create_agent("catalog", 1, starting_ref="cursor/catalog")
    assert calls[0]["model"] == "composer-2.5"
    assert "options" not in calls[0]
    assert not hasattr(calls[0]["cloud"], "mcp_servers")
    assert calls[0]["cloud"].repos[0].starting_ref == "cursor/catalog"


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
    assert commands[3][commands[3].index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
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
    assert fleet.created == 1
    assert len(state.run_ids) == 2
    assert (tmp_path / "out/catalog/extract.jsonl").exists()
    assert (tmp_path / "out/catalog/cutover_plan.json").exists()


def test_notion_status_failure_does_not_fail_slice_or_skip_state_save(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class RaisingStatusWriter:
        def record(
            self, _slice_name: str, _summary: dict[str, Any], _page_id: str | None
        ) -> str | None:
            raise RuntimeError("Notion unavailable")

    agent = FakeAgent("agent-1", [extract_json(), cutover_json()])
    migration = Migration(
        repo=Path(__file__).parents[1],
        fleet=FakeFleet(agent),
        gate=FakeGate(tmp_path),
        state=State(),
        state_path=tmp_path / "state.json",
        out_dir=tmp_path / "out",
        notion_writer=RaisingStatusWriter(),
    )

    assert migration.run(["catalog"]) == 0

    state = State.load(tmp_path / "state.json").slice("catalog")
    assert state.phase == "done"
    assert state.status == "done"
    assert "notion status update failed: Notion unavailable" in capsys.readouterr().out


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
