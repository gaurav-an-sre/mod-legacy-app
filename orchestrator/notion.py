"""Deterministic per-phase status in Notion, written by the orchestrator, not a model.

`off` is the default so the demo runs without a token. `api` writes plain REST blocks
whose content is fully determined by state; the agent-written narrative record is a
separate concern handled by the `notion_status` Run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

NOTION_VERSION = "2022-06-28"


class StatusWriter(Protocol):
    def record(self, slice_name: str, summary: dict[str, Any], page_id: str | None) -> str | None:
        """Record one phase transition; returns the page id to persist in state."""


class DisabledStatusWriter:
    def record(self, slice_name: str, summary: dict[str, Any], page_id: str | None) -> str | None:
        return page_id


@dataclass
class ApiStatusWriter:
    token: str
    parent_page_id: str
    client: Any = None
    pages: dict[str, str] = field(default_factory=dict)

    def _http(self) -> Any:
        if self.client is None:
            self.client = httpx.Client(timeout=20)
        return self.client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _create_page(self, slice_name: str) -> str:
        response = self._http().post(
            "https://api.notion.com/v1/pages",
            headers=self._headers(),
            json={
                "parent": {"page_id": self.parent_page_id},
                "properties": {
                    "title": {
                        "title": [
                            {
                                "type": "text",
                                "text": {"content": f"{slice_name} migration status"},
                            }
                        ]
                    }
                },
            },
        )
        _raise_for_status(response)
        return str(response.json()["id"])

    def record(self, slice_name: str, summary: dict[str, Any], page_id: str | None) -> str | None:
        target = page_id or self.pages.get(slice_name)
        if target is None:
            target = self._create_page(slice_name)
            self.pages[slice_name] = target
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        rate = summary.get("parity_rate")
        line = (
            f"{stamp} · phase {summary.get('phase')} · status {summary.get('status')} · "
            f"weight {summary.get('weight')}% · parity "
            f"{'not measured' if rate is None else f'{float(rate):.3f}'}"
        )
        response = self._http().patch(
            f"https://api.notion.com/v1/blocks/{target}/children",
            headers=self._headers(),
            json={
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]},
                    }
                ]
            },
        )
        _raise_for_status(response)
        return target


def _raise_for_status(response: Any) -> None:
    """Surface Notion's validation message; the bare status line hides the cause."""
    if response.status_code < 400:
        return
    raise RuntimeError(
        f"notion {response.request.method} {response.request.url} -> {response.text}"
    )


def build_status_writer(mode: str, token: str | None, parent_page_id: str | None) -> StatusWriter:
    if mode == "off":
        return DisabledStatusWriter()
    if not token:
        raise ValueError(f"--notion {mode} requires NOTION_TOKEN")
    if not parent_page_id:
        raise ValueError(f"--notion {mode} requires --notion-parent or NOTION_PARENT_PAGE_ID")
    return ApiStatusWriter(token=token, parent_page_id=parent_page_id)
