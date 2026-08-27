"""Thin wrapper over the Cursor SDK cloud agent surface.

One durable cloud agent per slice: created once, reused for every phase, so a later
phase inherits what the agent learned earlier. Nothing here runs an agent locally
except the Notion fallback runtime, which exists only in case stdio MCP is ever
unavailable inside the cloud sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEMO_TAG, MODEL


def notion_mcp_servers(token: str) -> dict[str, Any]:
    """The official Notion stdio server, as verified working in the cloud sandbox."""
    from cursor_sdk import StdioMcpServerConfig

    return {
        "notion": StdioMcpServerConfig(
            command="npx",
            args=["-y", "@notionhq/notion-mcp-server"],
            env={"NOTION_TOKEN": token},
        )
    }


@dataclass
class CloudFleet:
    """Creates, resumes, and lists the cloud agents of one migration."""

    repo_url: str
    api_key: str
    repo: Path = field(default_factory=Path)
    starting_ref: str = "main"
    model: str = MODEL
    auto_create_pr: bool = True
    client: Any = None

    def _sdk(self) -> Any:
        import cursor_sdk

        return cursor_sdk

    def _resolve_client(self) -> Any:
        """Default client path; explicit bridge wiring only if that path is unavailable."""
        if self.client is not None:
            return self.client
        sdk = self._sdk()
        try:
            self.client = sdk.Client(allow_api_key_env_fallback=True)
        except Exception:
            bridge = sdk.Bridge.launch(workspace=str(self.repo))
            self.client = sdk.Client(bridge.endpoint, allow_api_key_env_fallback=True)
        return self.client

    def create_agent(
        self,
        slice_name: str,
        wave: int,
        *,
        env_vars: dict[str, str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> Any:
        sdk = self._sdk()
        cloud = sdk.CloudAgentOptions(
            repos=[sdk.CloudRepository(url=self.repo_url, starting_ref=self.starting_ref)],
            auto_create_pr=self.auto_create_pr,
            env_vars=env_vars or {},
            metadata={"demo": DEMO_TAG, "slice": slice_name, "wave": str(wave)},
        )
        if mcp_servers is None:
            create_args = {
                "model": self.model,
                "api_key": self.api_key,
                "name": f"migrate {slice_name}",
                "cloud": cloud,
            }
            try:
                return sdk.Agent.create(**create_args)
            except Exception:
                return self._resolve_client().create_agent(**create_args)
        options = sdk.AgentOptions(
            model=self.model,
            api_key=self.api_key,
            name=f"migrate {slice_name}",
            cloud=cloud,
            mcp_servers=mcp_servers,
        )
        try:
            return sdk.Agent.create(options=options)
        except Exception:
            return self._resolve_client().create_agent(options=options)

    def resume_agent(self, agent_id: str) -> Any:
        sdk = self._sdk()
        try:
            return sdk.Agent.resume(agent_id)
        except Exception:
            return self._resolve_client().resume_agent(agent_id)

    def list_agents(self) -> list[Any]:
        sdk = self._sdk()
        try:
            listing = sdk.Agent.list()
        except Exception:
            listing = self._resolve_client().list_agents()
        agents = list(getattr(listing, "items", listing) or [])
        return [
            info for info in agents if dict(getattr(info, "metadata", {})).get("demo") == DEMO_TAG
        ]

    def local_notion_agent(self, slice_name: str, mcp_servers: dict[str, Any]) -> Any:
        """Fallback runtime for the Notion record if a cloud stdio MCP run ever fails."""
        sdk = self._sdk()
        options = sdk.AgentOptions(
            model=self.model,
            api_key=self.api_key,
            name=f"notion {slice_name}",
            local=sdk.LocalAgentOptions(cwd=str(self.repo)),
            mcp_servers=mcp_servers,
        )
        return sdk.Agent.create(options=options)
