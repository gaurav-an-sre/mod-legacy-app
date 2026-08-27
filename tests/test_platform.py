from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import console.main as console_main
from strangler.render import render_routes
from tools.cutover import _error_rates, promote, rollback
from tools.parity import _normalize, resolve_candidate_url

ROOT = Path(__file__).parents[1]


def test_routes_render_weighted_backend_and_mirror(tmp_path: Path) -> None:
    output = tmp_path / "nginx.conf"
    render_routes(ROOT / "strangler" / "routes.yaml", output)
    rendered = output.read_text(encoding="utf-8")
    assert "split_clients" in rendered
    assert "mirror /_shadow_catalog" in rendered
    assert "upstream candidate_catalog_upstream { server fake-candidate:8000; }" in rendered
    assert "backend=$migration_backend" in rendered
    assert "0% candidate" not in rendered


def test_non_idempotent_slice_is_not_mirrored(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    mirror: true\n"
        "    upstream: candidate_catalog\n"
        "    candidate: candidate_catalog:8001\n"
        "    routes: [/api/catalog/products]\n"
        "  orders:\n"
        "    weight: 0\n"
        "    mirror: false\n"
        "    upstream: candidate_orders\n"
        "    candidate: candidate_orders:8001\n"
        "    routes: [/api/orders/checkout]\n",
        encoding="utf-8",
    )
    output = tmp_path / "nginx.conf"
    render_routes(routes, output)
    rendered = output.read_text(encoding="utf-8")
    assert "mirror /_shadow_catalog" in rendered
    assert "location = /_shadow_catalog" in rendered
    assert "mirror /_shadow_orders" not in rendered
    assert "location = /_shadow_orders" not in rendered


def test_error_rates_ignore_samples_outside_soak_window(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    recent = datetime.now(UTC).replace(microsecond=0).isoformat()
    log.write_text(
        f"{recent} route=catalog backend=candidate status=200 latency_ms=0.01\n"
        "2024-01-01T00:00:00+00:00 route=catalog backend=candidate status=500 latency_ms=0.01\n"
        f"{recent} route=catalog backend=legacy status=500 latency_ms=0.01\n",
        encoding="utf-8",
    )
    assert _error_rates(log, soak_seconds=300) == (1.0, 0.0)
    assert _error_rates(log, soak_seconds=10**9) == (1.0, 0.5)


def test_normalization_removes_volatile_keys() -> None:
    value = {"id": 1, "created_at": "today", "nested": {"name": "kept"}}
    assert _normalize(value, {"id", "created_at"}) == {"nested": {"name": "kept"}}


def test_resolve_candidate_url_prefers_slice_config_over_compose_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  reports:\n"
        "    candidate: candidate-reports:8004\n"
        "    routes: [/api/reports/top-products]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CANDIDATE_URL", "http://fake-candidate:8000")
    assert (
        resolve_candidate_url("reports", routes_path=routes)
        == "http://candidate-reports:8004"
    )


def test_resolve_candidate_url_falls_back_to_env_without_slice_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  orders:\n"
        "    candidate: null\n"
        "    routes: [/api/orders/cart]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CANDIDATE_URL", "http://fake-candidate:8000")
    assert resolve_candidate_url("orders", routes_path=routes) == "http://fake-candidate:8000"


def test_error_rates_reads_legacy_and_candidate_access_log(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    log.write_text(
        "2024-01-01T00:00:00+00:00 route=catalog backend=legacy status=200 latency_ms=0.01\n"
        "2024-01-01T00:00:01+00:00 route=catalog backend=legacy status=500 latency_ms=0.01\n"
        "2024-01-01T00:00:02+00:00 route=catalog backend=candidate status=200 latency_ms=0.01\n",
        encoding="utf-8",
    )
    assert _error_rates(log, soak_seconds=10**9) == (0.5, 0.0)


def test_promote_rejects_low_parity_without_mutating_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    mirror: true\n"
        "    upstream: candidate\n"
        "    candidate: candidate:8001\n"
        "    routes: [/api/catalog/products]\n",
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(
        json.dumps({"candidate_url": "http://candidate:8001", "match_rate": 0.5}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover._reload", lambda _repo: None)
    with pytest.raises(SystemExit, match="below threshold"):
        promote("catalog", routes_path=routes, parity_path=report, repo=tmp_path)
    assert "weight: 0" in routes.read_text(encoding="utf-8")


def test_promote_rejects_slice_without_upstream_before_parity(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  reports:\n"
        "    weight: 0\n"
        "    upstream: null\n"
        "    candidate: null\n"
        "    routes: [/api/reports/top-products]\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="has no candidate upstream"):
        promote(
            "reports",
            routes_path=routes,
            parity_path=tmp_path / "missing.json",
            repo=tmp_path,
        )


def test_promote_rejects_parity_from_wrong_candidate(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    upstream: candidate\n"
        "    candidate: candidate:8001\n"
        "    routes: [/api/catalog/products]\n",
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(
        json.dumps({"candidate_url": "http://other:8001", "match_rate": 1.0}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="does not match configured candidate"):
        promote("catalog", routes_path=routes, parity_path=report, repo=tmp_path)


def test_promote_allows_matching_catalog_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    upstream: candidate\n"
        "    candidate: candidate:8001\n"
        "    routes: [/api/catalog/products]\n",
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(
        json.dumps({"candidate_url": "http://candidate:8001", "match_rate": 1.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover.subprocess.run", lambda *_args, **_kwargs: None)
    assert (
        promote(
            "catalog",
            routes_path=routes,
            parity_path=report,
            log_path=tmp_path / "access.log",
            repo=tmp_path,
        )
        == 5
    )
    assert "weight: 5" in routes.read_text(encoding="utf-8")


def test_promote_allows_real_catalog_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        (ROOT / "strangler" / "routes.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(
        json.dumps({"candidate_url": "http://fake-candidate:8000", "match_rate": 1.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover.subprocess.run", lambda *_args, **_kwargs: None)
    assert (
        promote(
            "catalog",
            routes_path=routes,
            parity_path=report,
            log_path=tmp_path / "access.log",
            repo=tmp_path,
        )
        == 5
    )


def test_rollback_allows_slice_without_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  reports:\n"
        "    weight: 100\n"
        "    upstream: null\n"
        "    routes: [/api/reports/top-products]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover.subprocess.run", lambda *_args, **_kwargs: None)
    rollback("reports", routes_path=routes, repo=tmp_path)
    assert "weight: 0" in routes.read_text(encoding="utf-8")


def test_console_reports_legacy_only_without_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "strangler").mkdir()
    (tmp_path / "parity").mkdir()
    (tmp_path / "strangler" / "routes.yaml").write_text(
        "slices:\n"
        "  reports:\n"
        "    weight: 100\n"
        "    upstream: null\n"
        "    routes: [/api/reports/top-products]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(console_main, "ROOT", tmp_path)
    rendered = console_main.migration_console()
    assert "Candidate: <code>legacy only</code>" in rendered
    assert "<b>0%</b> candidate traffic" in rendered
    assert 'style="width:100%"' not in rendered
