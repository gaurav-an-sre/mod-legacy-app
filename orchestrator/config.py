"""Fleet configuration and prompt variables for each migration slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_URL = "https://github.com/gaurav-an-sre/mod-legacy-app"
MODEL = "composer-2.5"
DEMO_TAG = "mod-legacy-app"
LEGACY_DIR = "legacy/"
DB_DSN_ENV = "MYSQL_DSN"
PARITY_THRESHOLD = 0.99
MAX_PARITY_ATTEMPTS = 2

PROMPTS = {
    "extract": "extract_slice.md",
    "parity_fix": "parity_fix.md",
    "cutover_plan": "cutover_plan.md",
    "notion_status": "notion_status.md",
}


@dataclass(frozen=True)
class SliceSpec:
    name: str
    description: str
    container_port: int


SLICES: dict[str, SliceSpec] = {
    "catalog": SliceSpec(
        "catalog",
        "product listing with search and paging, plus product detail",
        8001,
    ),
    "orders": SliceSpec(
        "orders",
        "cart contents and checkout, including the duplicated discount arithmetic",
        8002,
    ),
    "users": SliceSpec("users", "login and session establishment", 8003),
    "reports": SliceSpec("reports", "the admin top-products report", 8004),
}


def slice_routes(repo: Path, slice_name: str) -> list[str]:
    routes = yaml.safe_load((repo / "strangler" / "routes.yaml").read_text(encoding="utf-8"))
    return list(routes["slices"][slice_name]["routes"])


def slice_weight(repo: Path, slice_name: str) -> int:
    routes = yaml.safe_load((repo / "strangler" / "routes.yaml").read_text(encoding="utf-8"))
    return int(routes["slices"][slice_name]["weight"])


def parity_command(slice_name: str) -> str:
    return f"make parity SLICE={slice_name}"


def prompt_variables(
    repo: Path,
    slice_name: str,
    phase: str,
    *,
    parity_rate: float | None = None,
    parent_page_id: str = "",
) -> dict[str, str]:
    """Placeholder values for one phase prompt; the prompt text itself is never edited."""
    spec = SLICES[slice_name]
    out_dir = f"out/{slice_name}"
    common = {
        "slice": slice_name,
        "slice_description": spec.description,
        "legacy_dir": LEGACY_DIR,
        "legacy_routes": "\n".join(f"- `{route}`" for route in slice_routes(repo, slice_name)),
        "db_dsn_env": DB_DSN_ENV,
        "service_dir": f"services/{slice_name}",
        "container_port": str(spec.container_port),
        "parity_cmd": parity_command(slice_name),
        "parity_threshold": str(PARITY_THRESHOLD),
        "parity_report_path": f"parity/{slice_name}.json",
        "parity_match_rate": "unknown" if parity_rate is None else f"{parity_rate:.3f}",
        "extraction_json_path": f"{out_dir}/extract.json",
        "cutover_plan_path": f"{out_dir}/cutover_plan.json",
        "routes_yaml_path": "strangler/routes.yaml",
        "parent_page_id": parent_page_id,
    }
    if phase not in PROMPTS:
        raise ValueError(f"unknown phase: {phase}")
    return common
