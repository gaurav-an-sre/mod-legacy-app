from __future__ import annotations

import json
from pathlib import Path

import pytest

from strangler.render import render_routes
from tools.cutover import _error_rates, promote
from tools.parity import _normalize

ROOT = Path(__file__).parents[1]


def test_routes_render_weighted_backend_and_mirror(tmp_path: Path) -> None:
    output = tmp_path / "nginx.conf"
    render_routes(ROOT / "strangler" / "routes.yaml", output)
    rendered = output.read_text(encoding="utf-8")
    assert "split_clients" in rendered
    assert "mirror /_shadow_catalog" in rendered
    assert "backend=$migration_backend" in rendered
    assert "0% candidate" not in rendered


def test_normalization_removes_volatile_keys() -> None:
    value = {"id": 1, "created_at": "today", "nested": {"name": "kept"}}
    assert _normalize(value, {"id", "created_at"}) == {"nested": {"name": "kept"}}


def test_error_rates_reads_legacy_and_candidate_access_log(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    log.write_text(
        "2024 route=catalog backend=legacy status=200 latency_ms=0.01\n"
        "2024 route=catalog backend=legacy status=500 latency_ms=0.01\n"
        "2024 route=catalog backend=candidate status=200 latency_ms=0.01\n",
        encoding="utf-8",
    )
    assert _error_rates(log) == (0.5, 0.0)


def test_promote_rejects_low_parity_without_mutating_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    upstream: candidate\n"
        "    routes: [/api/catalog/products]\n",
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(json.dumps({"match_rate": 0.5}), encoding="utf-8")
    monkeypatch.setattr("tools.cutover._reload", lambda _repo: None)
    with pytest.raises(SystemExit, match="below threshold"):
        promote("catalog", routes_path=routes, parity_path=report, repo=tmp_path)
    assert "weight: 0" in routes.read_text(encoding="utf-8")
