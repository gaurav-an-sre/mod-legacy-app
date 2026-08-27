from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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

    def measure(self, _slice: str):
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
