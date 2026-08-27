"""Durable migration state: written after every phase transition."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

TERMINAL = ("done", "blocked", "failed")


@dataclass
class SliceState:
    name: str
    agent_id: str | None = None
    branch: str | None = None
    service_name: str | None = None
    container_port: int | None = None
    phase: str = "extract"
    status: str = "pending"
    run_ids: list[str] = field(default_factory=list)
    parity_rate: float | None = None
    parity_attempts: int = 0
    notion_page_id: str | None = None
    error: str | None = None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL


@dataclass
class State:
    slices: dict[str, SliceState] = field(default_factory=dict)

    def slice(self, name: str) -> SliceState:
        return self.slices.setdefault(name, SliceState(name=name))

    def to_json(self) -> dict[str, object]:
        return {"slices": {name: asdict(state) for name, state in sorted(self.slices.items())}}

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        slices = {
            name: SliceState(**{"name": name, **{k: v for k, v in value.items() if k != "name"}})
            for name, value in raw.get("slices", {}).items()
        }
        return cls(slices=slices)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
