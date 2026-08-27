"""The phase machine: durable, concurrent, and gated by the comparator."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from .config import (
    MAX_PARITY_ATTEMPTS,
    PROMPTS,
    SLICES,
    prompt_variables,
    slice_weight,
)
from .gate import MeasurementFailure, ParityGate
from .notion import DisabledStatusWriter, StatusWriter
from .sdk import notion_mcp_servers
from .state import SliceState, State
from .streaming import stream_run
from .validation import ContractError, parse_phase_reply

PROMPT_DIR = Path(__file__).parent / "prompts"


class PhaseFailure(RuntimeError):
    """A phase reply broke its contract, so the slice stops here."""


def render_prompt(phase: str, variables: dict[str, str]) -> str:
    text = (PROMPT_DIR / PROMPTS[phase]).read_text(encoding="utf-8")
    return Template(text).safe_substitute(**variables)


@dataclass
class Migration:
    repo: Path
    fleet: Any
    gate: ParityGate
    state: State
    state_path: Path
    out_dir: Path
    notion_mode: str = "off"
    notion_writer: StatusWriter = field(default_factory=DisabledStatusWriter)
    notion_token: str | None = None
    notion_parent_page_id: str = ""
    max_parity_attempts: int = MAX_PARITY_ATTEMPTS
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def run(self, slices: list[str], wave_size: int = 4) -> int:
        """Migrate slices concurrently in waves; one blocked slice never stops the others."""
        failures = 0
        for wave, batch in enumerate(_waves(slices, wave_size), start=1):
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results = list(
                    pool.map(
                        lambda name, current_wave=wave: self._guarded_slice(name, current_wave),
                        batch,
                    )
                )
            failures += sum(1 for ok in results if not ok)
        return failures

    def _guarded_slice(self, slice_name: str, wave: int) -> bool:
        try:
            self._run_slice(slice_name, wave)
        except Exception as exc:  # slice failures are isolated by design
            self._fail(slice_name, str(exc))
            print(f"[{slice_name}] failed: {exc}", flush=True)
            return False
        return self.state.slice(slice_name).status == "done"

    def _run_slice(self, slice_name: str, wave: int) -> None:
        if slice_name not in SLICES:
            raise ValueError(f"unknown slice: {slice_name}")
        st = self.state.slice(slice_name)
        if st.finished:
            print(f"[{slice_name}] already {st.status}, nothing to do", flush=True)
            return
        agent = self._agent(st, wave)
        st.status = "running"
        self._checkpoint(st)
        while not st.finished:
            phase = st.phase
            if phase == "extract":
                payload = self._phase(agent, st, "extract")
                st.branch = str(payload["branch"])
                st.service_name = str(payload["service_name"])
                st.container_port = int(payload["container_port"])
                st.phase = "gate"
            elif phase == "gate":
                self._gate(st)
            elif phase == "parity_fix":
                payload = self._phase(agent, st, "parity_fix")
                st.branch = str(payload["branch"])
                st.parity_attempts += 1
                st.phase = "gate"
            elif phase == "cutover_plan":
                self._phase(agent, st, "cutover_plan")
                st.phase = "notion_status"
            elif phase == "notion_status":
                if self.notion_mode == "mcp":
                    try:
                        payload = self._phase(agent, st, "notion_status")
                    except PhaseFailure:
                        raise
                    except Exception as exc:
                        if not self.notion_token:
                            raise
                        print(
                            f"[{slice_name}/notion_status] cloud MCP failed ({exc}); "
                            "retrying with local runtime",
                            flush=True,
                        )
                        payload = self._phase(
                            self.fleet.local_notion_agent(
                                slice_name, notion_mcp_servers(self.notion_token)
                            ),
                            st,
                            "notion_status",
                            event_name="notion_status_fallback",
                        )
                    st.notion_page_id = str(payload.get("page_id") or st.notion_page_id or "")
                    print(
                        f"[{slice_name}/notion_status] page {payload['page_url']}",
                        flush=True,
                    )
                else:
                    print(f"[{slice_name}] notion record skipped (--notion {self.notion_mode})")
                st.phase = "done"
                st.status = "done"
            else:
                raise ValueError(f"unknown phase in state: {phase}")
            self._checkpoint(st)

    def _agent(self, st: SliceState, wave: int) -> Any:
        if st.agent_id:
            print(f"[{st.name}] resuming agent {st.agent_id} at phase {st.phase}", flush=True)
            return self.fleet.resume_agent(st.agent_id)
        agent = self.fleet.create_agent(
            st.name,
            wave,
            env_vars=self._env_vars(),
            mcp_servers=self._mcp_servers(),
        )
        st.agent_id = str(getattr(agent, "agent_id", ""))
        print(f"[{st.name}] created agent {st.agent_id}", flush=True)
        self._checkpoint(st)
        return agent

    def _env_vars(self) -> dict[str, str]:
        if self.notion_mode == "mcp" and self.notion_token:
            return {"NOTION_TOKEN": self.notion_token}
        return {}

    def _mcp_servers(self) -> dict[str, Any] | None:
        if self.notion_mode == "mcp" and self.notion_token:
            return notion_mcp_servers(self.notion_token)
        return None

    def _phase(
        self,
        agent: Any,
        st: SliceState,
        phase: str,
        *,
        event_name: str | None = None,
    ) -> dict[str, Any]:
        slice_dir = self.out_dir / st.name
        variables = prompt_variables(
            self.repo,
            st.name,
            phase,
            parity_rate=st.parity_rate,
            parent_page_id=self.notion_parent_page_id,
        )
        label = f"{st.name}/{phase}"
        print(f"[{label}] starting", flush=True)
        text, run_id = stream_run(
            agent.send(render_prompt(phase, variables)),
            label,
            slice_dir / f"{event_name or phase}.jsonl",
        )
        if run_id:
            st.run_ids.append(run_id)
        try:
            payload = parse_phase_reply(phase, st.name, text)
        except ContractError as exc:
            slice_dir.mkdir(parents=True, exist_ok=True)
            (slice_dir / f"{phase}.raw.txt").write_text(text, encoding="utf-8")
            raise PhaseFailure(f"{phase} reply broke its contract: {exc}") from exc
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / f"{phase}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        return payload

    def _gate(self, st: SliceState) -> None:
        if not st.branch or not st.service_name or st.container_port is None:
            raise ValueError(f"{st.name} has no extracted branch/service metadata")
        report = self.gate.measure(
            st.name,
            branch=st.branch,
            service_name=st.service_name,
            container_port=st.container_port,
        )
        if report.get("measurement_error"):
            raise MeasurementFailure(f"{st.name} measurement failed: {report['measurement_error']}")
        st.parity_rate = self.gate.rate(report)
        if self.gate.passed(report):
            print(f"[{st.name}] gate passed at {st.parity_rate:.3f}", flush=True)
            st.phase = "cutover_plan"
            return
        if st.parity_attempts < self.max_parity_attempts:
            print(
                f"[{st.name}] gate failed at {st.parity_rate:.3f}, "
                f"parity_fix attempt {st.parity_attempts + 1}/{self.max_parity_attempts}",
                flush=True,
            )
            st.phase = "parity_fix"
            return
        print(f"[{st.name}] blocked at {st.parity_rate:.3f}, weight stays 0", flush=True)
        st.phase = "blocked"
        st.status = "blocked"

    def _fail(self, slice_name: str, error: str) -> None:
        st = self.state.slice(slice_name)
        st.status = "failed"
        st.phase = "failed"
        st.error = error
        self._checkpoint(st, write_notion=False)

    def _checkpoint(self, st: SliceState, *, write_notion: bool = True) -> None:
        with self._lock:
            self.state.save(self.state_path)
            if write_notion:
                st.notion_page_id = (
                    self.notion_writer.record(
                        st.name,
                        {
                            "phase": st.phase,
                            "status": st.status,
                            "parity_rate": st.parity_rate,
                            "weight": slice_weight(self.repo, st.name),
                        },
                        st.notion_page_id,
                    )
                    or st.notion_page_id
                )
            self.state.save(self.state_path)


def _waves(slices: list[str], wave_size: int) -> list[list[str]]:
    size = max(1, wave_size)
    return [slices[index : index + size] for index in range(0, len(slices), size)]
