from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import console.main as console_main
import console.shop as console_shop
from orchestrator.gate import ParityGate
from strangler.render import render_routes
from tools.cutover import _error_rates, promote, register, rollback
from tools.parity import _normalize, compare_slice
from tools.sawan_seed import ID_OFFSET, load_products, render_sql
from tools.wait_for_legacy import wait_for_legacy

ROOT = Path(__file__).parents[1]


def test_routes_render_weighted_backend_and_mirror(tmp_path: Path) -> None:
    output = tmp_path / "nginx.conf"
    render_routes(ROOT / "strangler" / "routes.yaml", output)
    rendered = output.read_text(encoding="utf-8")
    assert "split_clients" in rendered
    assert "mirror /_shadow_catalog" in rendered
    assert "candidate http://candidate-catalog:8001;" in rendered
    assert "resolver 127.0.0.11" in rendered
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
    assert "$x_upstream" not in rendered
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
    def __init__(
        self,
        status_code: int,
        body: str,
        *,
        json_body: object | None = None,
        content_type: str = "text/plain",
    ) -> None:
        self.status_code = status_code
        self.text = body
        self.json_body = json_body
        self.headers = {"content-type": content_type}

    def json(self) -> object | None:
        return self.json_body


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


def test_parity_accepts_matching_legacy_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _compare_one_request(
        tmp_path,
        monkeypatch,
        [
            FakeParityResponse(401, "invalid credentials"),
            FakeParityResponse(401, "invalid credentials"),
        ],
    )
    assert "measurement_error" not in report
    assert report["match_rate"] == 1.0


def test_parity_ignores_error_markers_in_json_legacy_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _compare_one_request(
        tmp_path,
        monkeypatch,
        [
            FakeParityResponse(
                200,
                '{"message":"Uncaught product warning"}',
                json_body={"message": "Uncaught product warning"},
                content_type="application/json",
            ),
            FakeParityResponse(
                200,
                '{"message":"Uncaught product warning"}',
                json_body={"message": "Uncaught product warning"},
                content_type="application/json",
            ),
        ],
    )
    assert "measurement_error" not in report
    assert report["match_rate"] == 1.0


def test_wait_for_legacy_retries_php_error_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = tmp_path / "requests.yaml"
    requests.write_text("catalog:\n  - method: GET\n    path: /health\n", encoding="utf-8")

    class FakeClient:
        def __init__(self, responses: list[FakeParityResponse]) -> None:
            self.responses = responses

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> FakeParityResponse:
            return self.responses.pop(0)

    client = FakeClient(
        [
            FakeParityResponse(200, "<b>Fatal error</b>: mysqli_sql_exception"),
            FakeParityResponse(200, "healthy"),
        ]
    )
    monkeypatch.setattr("tools.wait_for_legacy.httpx.Client", lambda **_kwargs: client)
    monkeypatch.setattr("tools.wait_for_legacy.time.sleep", lambda _seconds: None)

    wait_for_legacy("catalog", requests_path=requests, legacy_url="http://legacy", interval=0)


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
        json.dumps({"candidate_url": "http://candidate-catalog:8001", "match_rate": 1.0}),
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


HOOK = ROOT / ".cursor" / "hooks" / "deny_protected_writes.py"


def _hook(payload: dict) -> dict:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(proc.stdout)


def test_hooks_json_uses_cursor_schema() -> None:
    config = json.loads((ROOT / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert config["version"] == 1
    assert {"preToolUse", "beforeShellExecution"} <= set(config["hooks"])
    for entries in config["hooks"].values():
        for entry in entries:
            assert entry["failClosed"] is True
            assert entry["command"].endswith("deny_protected_writes.py")


@pytest.mark.parametrize(
    ("payload", "permission"),
    [
        ({"tool_name": "Write", "tool_input": {"path": "legacy/index.php"}}, "deny"),
        ({"tool_name": "Delete", "tool_input": {"path": "db/seed.sql"}}, "deny"),
        ({"tool_name": "Write", "tool_input": {"path": "strangler/routes.yaml"}}, "deny"),
        ({"tool_name": "Write", "tool_input": {"path": "services/catalog/app.py"}}, "allow"),
        ({"command": "sed -i 's/a/b/' legacy/index.php", "cwd": "/w"}, "deny"),
        ({"command": "echo x >> db/seed.sql", "cwd": "/w"}, "deny"),
        ({"command": "cat legacy/includes/db.php", "cwd": "/w"}, "allow"),
        ({"command": "make parity SLICE=catalog", "cwd": "/w"}, "allow"),
        ({"command": "cat x && rm -rf legacy/", "cwd": "/w"}, "deny"),
        ({"command": "cat legacy/index.php | tee db/seed.sql", "cwd": "/w"}, "deny"),
        ({"command": "cd legacy && sed -i s/a/b/ index.php", "cwd": "/w"}, "deny"),
        ({"command": "rm index.php", "cwd": "/w/legacy", "workspace_roots": ["/w"]}, "deny"),
        ({"command": "echo x > seed.sql", "cwd": "/w/db/", "workspace_roots": ["/w"]}, "deny"),
        ({"command": "pytest", "cwd": "/w/services/catalog", "workspace_roots": ["/w"]}, "allow"),
        ({"command": "docker compose up -d db", "cwd": "/w", "workspace_roots": ["/w"]}, "allow"),
        ({"command": "find . -delete", "cwd": "/w/legacy", "workspace_roots": ["/w"]}, "deny"),
        ({"command": "cat index.php", "cwd": "/w/legacy", "workspace_roots": ["/w"]}, "allow"),
    ],
)
def test_protected_paths_hook(payload: dict, permission: str) -> None:
    assert _hook(payload)["permission"] == permission


def test_register_points_slice_at_candidate_without_moving_weight(tmp_path: Path) -> None:
    routes = tmp_path / "routes.yaml"
    routes.write_text((ROOT / "strangler" / "routes.yaml").read_text(encoding="utf-8"))
    (tmp_path / "strangler").mkdir()
    (tmp_path / "strangler" / "render.py").write_text("raise SystemExit(0)\n")
    register("orders", "candidate-orders", 8000, routes_path=routes, repo=tmp_path, reload=False)
    import yaml

    config = yaml.safe_load(routes.read_text(encoding="utf-8"))
    assert config["slices"]["orders"]["candidate"] == "candidate-orders:8000"
    assert config["slices"]["orders"]["upstream"] == "candidate_orders"
    assert config["slices"]["orders"]["weight"] == 0


def _console_fixture(tmp_path: Path) -> None:
    (tmp_path / "strangler" / "logs").mkdir(parents=True)
    (tmp_path / "parity").mkdir()
    (tmp_path / "search_eval").mkdir()
    (tmp_path / "out" / "catalog").mkdir(parents=True)
    (tmp_path / "strangler" / "routes.yaml").write_text(
        "slices:\n"
        "  catalog:\n"
        "    weight: 50\n"
        "    mirror: true\n"
        "    upstream: candidate_catalog\n"
        "    candidate: candidate-catalog:8001\n"
        "    routes: [/api/catalog/products]\n",
        encoding="utf-8",
    )
    (tmp_path / "strangler" / "logs" / "access.log").write_text(
        "t route=catalog backend=legacy status=200 latency_ms=1\n"
        "t route=catalog backend=candidate status=200 latency_ms=1\n"
        "t route=catalog backend=candidate status=502 latency_ms=1\n"
        "garbage line\n",
        encoding="utf-8",
    )
    (tmp_path / "parity" / "catalog.json").write_text(
        json.dumps({"match_rate": 1.0, "matched": 10, "total": 10}), encoding="utf-8"
    )
    (tmp_path / "search_eval" / "catalog.json").write_text(
        json.dumps(
            {
                "passed": True,
                "threshold": 0.9,
                "k": 3,
                "queries": [{}] * 19,
                "summary": {
                    "legacy": {"exact": 1.0, "tone_marks": 0.0, "overall": 0.421},
                    "enhanced": {"exact": 1.0, "tone_marks": 1.0, "overall": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "out" / "state.json").write_text(
        json.dumps(
            {
                "slices": {
                    "catalog": {
                        "name": "catalog",
                        "agent_id": "bc-123",
                        "runtime": "cloud",
                        "branch": "cursor/extract-catalog",
                        "pr_url": "https://github.com/x/y/pull/16",
                        "phase": "done",
                        "status": "done",
                        "run_ids": ["run-1", "run-2"],
                        "parity_attempts": 0,
                        "usage": {"charged_cents": 109.7, "total_tokens": 1564355, "runs": 4},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "out" / "catalog" / "extract.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "out" / "catalog" / "cutover_plan.jsonl").write_text(
        '{"type": "status", "status": "RUNNING"}\n'
        "not json\n"
        '{"type": "tool_call", "name": "read_file", "status": "completed", '
        '"args": {"path": "/workspace/strangler/routes.yaml"}}\n'
        '{"type": "tool_call", "name": "shell", "status": "completed", '
        '"args": {"command": "make parity SLICE=catalog"}}\n'
        '{"type": "assistant", "text": "done"}\n',
        encoding="utf-8",
    )


def test_console_state_reads_real_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _console_fixture(tmp_path)
    monkeypatch.setattr(console_main, "ROOT", tmp_path)
    payload = console_main.migration_state()
    (catalog,) = payload["slices"]
    assert catalog["weight"] == 50
    assert catalog["counts"] == {"legacy": 1, "candidate": 2, "errors": 1}
    assert catalog["agent"]["pr_url"] == "https://github.com/x/y/pull/16"
    assert catalog["agent"]["runtime"] == "cloud"
    assert catalog["agent"]["runs"] == 2
    assert catalog["events"]["total"] == 4
    assert catalog["events"]["phases"]["cutover_plan"]["tool_calls"] == 2
    assert catalog["events"]["recent"][-1] == {
        "phase": "cutover_plan",
        "type": "assistant",
        "text": "done",
    }
    assert catalog["search_eval"]["legacy_overall"] == 0.421
    assert catalog["search_eval"]["enhanced_overall"] == 1.0
    assert payload["totals"]["charged_cents"] == 109.7
    assert payload["totals"]["gate_ready"] == 1
    assert payload["totals"]["serving"] == 1


def test_console_html_shows_agent_search_and_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _console_fixture(tmp_path)
    monkeypatch.setattr(console_main, "ROOT", tmp_path)
    rendered = console_main.migration_console()
    assert "RAMPING 50%" in rendered
    assert "bc-123" in rendered
    assert 'href="https://github.com/x/y/pull/16"' in rendered
    assert "$1.10" in rendered
    assert "42%</b> → enhanced <b class=good>100%" in rendered
    assert "read_file×1" in rendered


def test_console_without_orchestrator_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "strangler").mkdir()
    (tmp_path / "strangler" / "routes.yaml").write_text(
        "slices:\n  orders:\n    weight: 0\n    routes: [/api/orders]\n", encoding="utf-8"
    )
    monkeypatch.setattr(console_main, "ROOT", tmp_path)
    rendered = console_main.migration_console()
    assert "No agent has been dispatched" in rendered
    assert "not recorded" in rendered
    assert console_main.migration_state()["totals"]["agents"] == 0


def test_sawan_seed_sql_stays_clear_of_monolith_ids() -> None:
    products = load_products()
    sql = render_sql(products)
    assert sql.startswith("SET NAMES utf8mb4;")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert sql.count("\n(") == len(products)
    assert f"({ID_OFFSET + 1}, 'SM-FS-001'" in sql
    assert all(int(p["id"]) + ID_OFFSET > 100 for p in products)
    assert "'it''s'" in render_sql([{**products[0], "name": "it's"}])


def _shop_transport(served_by: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "legacy":
            return httpx.Response(200, json={"products": []})
        if host == "candidate":
            return httpx.Response(
                200,
                json={"products": [{"id": 108, "sku": "SM-DR-001", "name": "Coke", "price": "32"}]},
            )
        if host == "facade":
            return httpx.Response(
                200, json={"products": []}, headers={"X-Migration-Served": served_by}
            )
        raise httpx.ConnectError("down")

    return httpx.MockTransport(handler)


def test_shop_page_compares_backends_and_shows_facade_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHOP_LEGACY_URL", "http://legacy")
    monkeypatch.setenv("SHOP_CANDIDATE_URL", "http://candidate")
    monkeypatch.setenv("SHOP_FACADE_URL", "http://facade")
    monkeypatch.setenv("SHOP_FACADE_PUBLIC_URL", "http://localhost:8080")
    columns = console_shop.fetch_results("โค้ก", transport=_shop_transport("legacy"))
    assert [len(c["items"]) for c in columns] == [0, 1, 0]
    assert columns[2]["served_by"] == "legacy"
    assert columns[2]["url"].startswith("http://localhost:8080/api/catalog/products?q=")
    rendered = console_shop.render_shop("โค้ก", columns, 5)
    assert "weight: <b>5%" in rendered
    assert "served by legacy" in rendered
    assert "SM-DR-001" in rendered
    assert rendered.count("0 results") >= 2


def test_shop_page_unreachable_backend_is_a_column_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHOP_LEGACY_URL", "http://nowhere")
    monkeypatch.setenv("SHOP_CANDIDATE_URL", "http://candidate")
    monkeypatch.setenv("SHOP_FACADE_URL", "http://facade")
    rendered = console_shop.shop_page("mug", 100, transport=_shop_transport("candidate"))
    assert "unreachable — ConnectError" in rendered
    assert "served by candidate" in rendered
    assert "<script" not in rendered


def test_shop_page_without_query_does_not_call_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        console_shop, "fetch_results", lambda *a: pytest.fail("backends must not be called")
    )
    rendered = console_shop.shop_page("  ", None)
    assert "type a query" in rendered
    assert "weight: <b>n/a" in rendered
