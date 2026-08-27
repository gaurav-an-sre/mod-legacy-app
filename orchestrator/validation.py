"""Strict per-phase contract validation. Invalid replies fail the slice, never repaired."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any


class ContractError(ValueError):
    """A phase reply did not satisfy the contract printed in its prompt."""


BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def strip_json_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractError(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{key} must be a non-empty string")
    return value


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{key} must be an integer")
    return value


def _rate(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{key} must be a number between 0 and 1")
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ContractError(f"{key} must be between 0 and 1, got {rate}")
    return rate


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{key} must be a list of strings")
    return list(value)


def _object_list(payload: dict[str, Any], key: str, keys: Iterable[str]) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ContractError(f"{key} must be a list of objects")
    for item in value:
        if not isinstance(item, dict):
            raise ContractError(f"{key} entries must be objects")
        missing = [name for name in keys if name not in item]
        if missing:
            raise ContractError(f"{key} entries are missing {', '.join(missing)}")
    return list(value)


def _slice_matches(payload: dict[str, Any], slice_name: str) -> None:
    if _string(payload, "slice") != slice_name:
        raise ContractError(f"slice must be {slice_name}, got {payload.get('slice')!r}")


def _branch(payload: dict[str, Any]) -> str:
    branch = _string(payload, "branch")
    if (
        len(branch) > 200
        or not BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or branch.endswith(("/", ".lock"))
    ):
        raise ContractError("branch is not a valid branch name")
    return branch


def validate_extract(payload: Any, slice_name: str) -> dict[str, Any]:
    data = _mapping(payload)
    _slice_matches(data, slice_name)
    _string(data, "service_name")
    _branch(data)
    container_port = _integer(data, "container_port")
    if not 1 <= container_port <= 65535:
        raise ContractError("container_port must be between 1 and 65535")
    _string_list(data, "routes")
    _rate(data, "parity_match_rate")
    _object_list(data, "unresolved_differences", ("route", "why"))
    _string_list(data, "notes")
    return data


def validate_parity_fix(payload: Any, slice_name: str) -> dict[str, Any]:
    data = _mapping(payload)
    _slice_matches(data, slice_name)
    _branch(data)
    _rate(data, "parity_match_rate")
    _object_list(data, "fixed", ("route", "cause", "fix"))
    _object_list(data, "benign", ("route", "why"))
    _object_list(data, "still_failing", ("route", "why"))
    return data


def validate_cutover_plan(payload: Any, slice_name: str) -> dict[str, Any]:
    data = _mapping(payload)
    _slice_matches(data, slice_name)
    steps = _object_list(data, "steps", ("weight", "soak_minutes", "watch"))
    for step in steps:
        weight = step["weight"]
        if isinstance(weight, bool) or not isinstance(weight, int) or not 0 <= weight <= 100:
            raise ContractError("steps[].weight must be an integer between 0 and 100")
        if isinstance(step["soak_minutes"], bool) or not isinstance(step["soak_minutes"], int):
            raise ContractError("steps[].soak_minutes must be an integer")
        if not isinstance(step["watch"], list) or not step["watch"]:
            raise ContractError("steps[].watch must be a non-empty list of observable signals")
    _object_list(data, "rollback_triggers", ("signal", "threshold", "action"))
    _object_list(data, "irreversible_operations", ("what", "why_risky"))
    _string(data, "residual_risk")
    return data


def validate_notion_status(payload: Any, slice_name: str) -> dict[str, Any]:
    data = _mapping(payload)
    _slice_matches(data, slice_name)
    _string(data, "page_id")
    _string(data, "page_url")
    if not isinstance(data.get("created"), bool):
        raise ContractError("created must be a boolean")
    return data


VALIDATORS = {
    "extract": validate_extract,
    "parity_fix": validate_parity_fix,
    "cutover_plan": validate_cutover_plan,
    "notion_status": validate_notion_status,
}


def parse_phase_reply(phase: str, slice_name: str, text: str) -> dict[str, Any]:
    validator = VALIDATORS.get(phase)
    if validator is None:
        raise ContractError(f"no contract for phase {phase}")
    try:
        payload = json.loads(strip_json_fence(text))
    except json.JSONDecodeError as exc:
        raise ContractError(f"reply is not valid JSON: {exc}") from exc
    return validator(payload, slice_name)
