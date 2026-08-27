from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import console.main as console_main
from orchestrator.gate import ParityGate
from strangler.render import render_routes
from tools.cutover import _error_rates, promote, rollback
from tools.parity import _normalize, compare_slice

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


def test_incomplete_candidate_renders_as_legacy(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  broken:\n"
        "    weight: 100\n"
        "    mirror: true\n"
        "    upstream: candidate_x\n"
        "    candidate: null\n"
        "    routes: [/api/broken]\n",
        encoding="utf-8",
    )
    output = tmp_path / "nginx.conf"
    render_routes(routes, output)
    rendered = output.read_text(encoding="utf-8")
    assert "candidate_x_upstream" not in rendered
    assert "location = /api/broken" in rendered
    assert "proxy_pass http://legacy_upstream;" in rendered


def test_error_rates_ignore_samples_outside_soak_window(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    recent = datetime.now(UTC).replace(microsecond=0).isoformat()
    log.write_text(
        f"{recent} route=catalog backend=candidate status=200 latency_ms=0.01\n"
        "2024-01-01T00:00:00+00:00 route=catalog backend=candidate status=500 latency_ms=0.01\n"
        f"{recent} route=catalog backend=legacy status=500 latency_ms=0.01\n",
        encoding="utf-8",
    )
    assert _error_rates(log, "catalog", soak_seconds=300) == (1.0, 0.0)
    assert _error_rates(log, "catalog", soak_seconds=10**9) == (1.0, 0.5)


def test_normalization_removes_volatile_keys() -> None:
    value = {"id": 1, "created_at": "today", "nested": {"name": "kept"}}
    assert _normalize(value, {"id", "created_at"}) == {"nested": {"name": "kept"}}


class FakeParityResponse:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = {"content-type": "text/plain"}


class FakeParityClient:
    def __init__(self, responses: list[FakeParityResponse]) -> None:
        self.responses = responses

    def __enter__(self) -> FakeParityClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def request(self, *_args: object, **_kwargs: object) -> FakeParityResponse:
        return self.responses.pop(0)


def _compare_one_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[FakeParityResponse],
) -> dict[str, object]:
    requests = tmp_path / "requests.yaml"
    requests.write_text("catalog:\n  - method: GET\n    path: /health\n", encoding="utf-8")
    normalize = tmp_path / "normalize.yaml"
    normalize.write_text("ignore_json_keys: []\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.parity.httpx.Client",
        lambda **_kwargs: FakeParityClient(responses),
    )
    return compare_slice("catalog", requests, normalize)


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (500, "database unavailable"),
        (200, "<b>Fatal error</b>: Uncaught mysqli_sql_exception: Connection refused"),
    ],
)
def test_parity_rejects_unhealthy_legacy_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: str,
) -> None:
    report = _compare_one_request(
        tmp_path,
        monkeypatch,
        [FakeParityResponse(status_code, body)],
    )
    assert "measurement_error" in report
    assert not ParityGate(tmp_path).passed(report)
    assert report["match_rate"] == 0.0


def test_parity_compares_healthy_legacy_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _compare_one_request(
        tmp_path,
        monkeypatch,
        [
            FakeParityResponse(200, "healthy"),
            FakeParityResponse(200, "healthy"),
        ],
    )
    assert "measurement_error" not in report
    assert report["match_rate"] == 1.0
    assert ParityGate(tmp_path).passed(report)


def test_error_rates_reads_legacy_and_candidate_access_log(tmp_path: Path) -> None:
    log = tmp_path / "access.log"
    log.write_text(
        "2024-01-01T00:00:00+00:00 route=catalog backend=legacy status=200 latency_ms=0.01\n"
        "2024-01-01T00:00:01+00:00 route=catalog backend=legacy status=500 latency_ms=0.01\n"
        "2024-01-01T00:00:02+00:00 route=catalog backend=candidate status=200 latency_ms=0.01\n",
        encoding="utf-8",
    )
    assert _error_rates(log, "catalog", soak_seconds=10**9) == (0.5, 0.0)


def test_promote_ignores_other_slice_candidate_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 0\n"
        "    upstream: candidate\n"
        "    candidate: candidate:8001\n"
        "    routes: [/api/catalog/products]\n"
        "  orders:\n"
        "    weight: 0\n"
        "    upstream: orders\n"
        "    candidate: orders:8001\n"
        "    routes: [/api/orders/cart]\n",
        encoding="utf-8",
    )
    report = tmp_path / "catalog.json"
    report.write_text(
        json.dumps({"candidate_url": "http://candidate:8001", "match_rate": 1.0}),
        encoding="utf-8",
    )
    log = tmp_path / "access.log"
    log.write_text(
        "2024-01-01T00:00:00+00:00 route=orders backend=candidate status=500 latency_ms=0.01\n"
        "2024-01-01T00:00:01+00:00 route=catalog backend=candidate status=200 latency_ms=0.01\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover.subprocess.run", lambda *_args, **_kwargs: None)

    assert (
        promote(
            "catalog",
            routes_path=routes,
            parity_path=report,
            log_path=log,
            soak_seconds=10**9,
            repo=tmp_path,
        )
        == 5
    )


def test_promote_rejects_candidate_errors_for_slice(
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
    log = tmp_path / "access.log"
    log.write_text(
        "2024-01-01T00:00:00+00:00 route=catalog backend=candidate status=500 latency_ms=0.01\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.cutover.subprocess.run", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit, match="candidate error rate"):
        promote(
            "catalog",
            routes_path=routes,
            parity_path=report,
            log_path=log,
            soak_seconds=10**9,
            repo=tmp_path,
        )


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


def test_promote_rejects_slice_without_candidate(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "slices:\n"
        "  broken:\n"
        "    weight: 0\n"
        "    upstream: candidate_x\n"
        "    candidate: null\n"
        "    routes: [/api/broken]\n",
        encoding="utf-8",
    )
    report = tmp_path / "broken.json"
    report.write_text(
        json.dumps({"candidate_url": "http://candidate_x:8000", "match_rate": 1.0}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="configured candidate missing"):
        promote("broken", routes_path=routes, parity_path=report, repo=tmp_path)


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


def test_console_reports_legacy_only_without_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "strangler").mkdir()
    (tmp_path / "parity").mkdir()
    (tmp_path / "strangler" / "routes.yaml").write_text(
        "slices:\n"
        "  broken:\n"
        "    weight: 100\n"
        "    upstream: candidate_x\n"
        "    candidate: null\n"
        "    routes: [/api/broken]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(console_main, "ROOT", tmp_path)
    rendered = console_main.migration_console()
    assert "Candidate: <code>legacy only</code>" in rendered
    assert "<b>0%</b> candidate traffic" in rendered
    assert 'style="width:100%"' not in rendered
