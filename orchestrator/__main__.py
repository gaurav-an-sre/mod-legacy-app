"""CLI for the cloud migration fleet."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import SLICES
from .gate import ParityGate
from .migrate import Migration
from .notion import build_status_writer
from .sdk import CloudFleet
from .state import State


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status", "resume"))
    parser.add_argument("--slices", default=",".join(SLICES))
    parser.add_argument("--wave-size", type=int, default=4)
    parser.add_argument("--repo-url", default="https://github.com/gaurav-an-sre/mod-legacy-app")
    parser.add_argument("--starting-ref", default="main")
    parser.add_argument("--state", type=Path, default=Path("out/state.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument("--notion", choices=("off", "api", "mcp"), default="off")
    parser.add_argument("--notion-parent", default=os.getenv("NOTION_PARENT_PAGE_ID"))
    return parser


def _selected(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in SLICES]
    if unknown:
        raise SystemExit(f"unknown slice(s): {', '.join(unknown)}")
    if not names:
        raise SystemExit("--slices must contain at least one slice")
    return names


def _fleet(args: argparse.Namespace) -> CloudFleet:
    key = os.getenv("CURSOR_API_KEY")
    if not key:
        raise SystemExit("CURSOR_API_KEY is required for cloud orchestration")
    return CloudFleet(
        repo_url=args.repo_url,
        api_key=key,
        repo=Path.cwd(),
        starting_ref=args.starting_ref,
    )


def _migration(args: argparse.Namespace) -> Migration:
    token = os.getenv("NOTION_TOKEN")
    if args.notion in {"api", "mcp"} and not token:
        raise SystemExit(f"--notion {args.notion} requires NOTION_TOKEN")
    if args.notion in {"api", "mcp"} and not args.notion_parent:
        raise SystemExit(
            f"--notion {args.notion} requires --notion-parent or NOTION_PARENT_PAGE_ID"
        )
    repo = Path.cwd()
    return Migration(
        repo=repo,
        fleet=_fleet(args),
        gate=ParityGate(repo),
        state=State.load(args.state),
        state_path=args.state,
        out_dir=args.out_dir,
        notion_mode=args.notion,
        notion_writer=build_status_writer(args.notion, token, args.notion_parent),
        notion_token=token,
        notion_parent_page_id=args.notion_parent or "",
    )


def main() -> int:
    args = _parser().parse_args()
    if args.command == "status":
        fleet = _fleet(args)
        for agent in fleet.list_agents():
            metadata = dict(getattr(agent, "metadata", {}))
            print(
                f"{getattr(agent, 'agent_id', '?')} {getattr(agent, 'status', '?')} "
                f"{metadata.get('slice', '?')} wave={metadata.get('wave', '?')}"
            )
        state = State.load(args.state)
        for name, state_item in state.slices.items():
            print(
                f"state {name}: {state_item.status} phase={state_item.phase} "
                f"parity={state_item.parity_rate} agent={state_item.agent_id}"
            )
        return 0
    migration = _migration(args)
    slices = _selected(args.slices)
    result = migration.run(slices, wave_size=args.wave_size)
    return 1 if result else 0


if __name__ == "__main__":
    sys.exit(main())
