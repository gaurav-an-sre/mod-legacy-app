# mod-legacy-app

This is a local strangler-fig modernization demo: a deliberately old PHP/MySQL
shop runs behind an nginx façade while Cursor cloud agents extract one route
slice at a time into `services/`. The platform is useful before an agent is
involved: the included fake candidate demonstrates parity, promotion, rollback,
and the migration console.

## Start the platform

Requirements: Docker Desktop or Docker Engine with Compose, and Python 3.11
with a local `.venv`.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
make up
make seed
```

Only the façade publishes a host port: open
[http://localhost:8080/](http://localhost:8080/) for the dated storefront and
[http://localhost:8080/_migration](http://localhost:8080/_migration) for the
auto-refreshing migration console. The deterministic database is initialized
from `db/schema.sql` and `db/seed.sql`; `make seed` is safe to repeat.

The legacy app has HTML pages for catalog, product details, cart, checkout,
login, and an admin report. Its JSON surfaces are grouped into catalog, orders,
users, and reports. The intentionally visible legacy smells include a global
mysqli handle, SQL in page scripts, file-backed PHP sessions, and duplicated
pricing behavior.

## Parity and cutover demo

`services/catalog/` is the first real extracted slice (built by a Cursor Cloud
Agent) and `strangler/routes.yaml` already points `catalog` at it at weight 0.
`make cutover-demo` rehearses the whole lifecycle against the live stack:
health check, fresh parity measurement, controller-owned registration, soak at
0%, gated promotion 5 → 50 → 100 with real traffic counted per backend from the
façade's own log, and rollback. `FAILURE_DRILL=1 make cutover-demo` additionally
stops the candidate at 5%, shows `promote` refusing because candidate 5xx
exceeds legacy, and rolls back; the façade resolves candidates through Docker
DNS per request, so rollback works even when the candidate container is gone.

The candidate in `tests/fixtures/fake_candidate/` is not an extracted service.
It is a tiny fixture used to make the platform verifiable without a Cursor
agent. It deliberately returns a differently rounded price by default.

```sh
# Replay recorded traffic against the real catalog candidate (CANDIDATE_URL in compose.yaml).
make parity

# To see the gate fail, point it at the divergent fixture instead.
docker compose --profile tools run --rm -e CANDIDATE_URL=http://fake-candidate:8000 parity \
  python tools/parity.py --slice catalog

# Promote through 5%, 50%, and 100%; each command advances one step.
make promote
make promote
make promote

# Return all catalog traffic to the monolith without restarting nginx.
make rollback
```

`nginx -s reload` is graceful: for roughly the first 50 ms after rollback
returns, a few in-flight requests may still be handled by the previous worker.
In testing, about 4 of 60 requests in that initial window still reached the
candidate (all returned 200); traffic then settled at 100% legacy.

`tools/parity.py` replays `traffic/requests.yaml`, compares status and normalized
JSON/text bodies, and writes a self-explanatory result for every request to
`parity/<slice>.json`. Normalization (`traffic/normalize.yaml`) drops only
genuinely volatile fields — `order_id`, `created_at`, `timestamp`, and the
`set-cookie`/`date` headers. Product ids are load-bearing catalog behaviour and
are compared, so a candidate that returns the wrong ids fails parity.

The promotion gate is decided by this replay comparator, not by diffing mirrored
responses. Mirroring exercises a candidate with real load before it receives
real users; it is not the parity decision. Mirroring is per-slice
(`mirror: true|false` in `strangler/routes.yaml`): the non-idempotent `orders`
and `users` slices are deliberately **not** shadowed, because a mirrored
`POST /api/orders/checkout` would apply the side effect twice. Those slices are
gated by replay parity alone.

The controller owns the weights `[0, 5, 50, 100]` in `strangler/routes.yaml`.
`tools/cutover.py` requires the latest parity report to meet the threshold
(default `0.99`) and requires the candidate 5xx rate in the façade access log to
be no greater than legacy's over the trailing soak window (`--soak-seconds`,
default 300). Be aware that a slice with no candidate samples inside that window
passes the error gate trivially — with zero requests there is nothing to fail on,
so replay parity is the real gate there. The controller rewrites the nginx
configuration and runs `nginx -s reload` through Compose, rather than restarting
the façade.

Every `make` target that takes a slice honours `SLICE` (default `catalog`), for
example `make parity SLICE=orders` or `make promote SLICE=reports`.

## Search evaluation (Sawan Mart)

Parity proves the candidate is bug-for-bug legacy. `make search-eval` is the
second deterministic gate: it proves the candidate's *enhanced* search is
better without regressing. `search_eval/sawan_mart/` holds a fixed Thai grocery
catalog, a synonym map, and golden queries grouped by category — exact,
missing/reordered tone marks (`นำปลา`, `นํ้าปลา` → `น้ำปลา`), brand/generic
synonyms (`โค้ก` → Coca-Cola), autocomplete prefixes, and intent queries such as
`ของว่าง` (snacks). `tools/search_eval.py` scores recall@3 for the legacy
`LIKE '%q%'` behaviour and for `services/catalog/search.py`, writes
`search_eval/catalog.json`, and exits non-zero if the enhanced score is below
the threshold or is worse than legacy in any category. No model is involved, so
the same fixtures give the same numbers on every run and in CI.

The candidate serves legacy behaviour by default (`SEARCH_MODE=legacy`, which is
what parity measures). Setting `SEARCH_MODE=enhanced` (and optionally
`SEARCH_SYNONYMS`; `compose.yaml` wires both, so `SEARCH_MODE=enhanced docker compose up -d candidate-catalog` is enough) on the `candidate-catalog` service
switches `/api/catalog/products?q=` to the ranked, tone-mark-insensitive search
once the slice is at 100%.

## Cursor agent handoff

`services/` intentionally contains only a README. Extraction agents should
write modernized services there, but must never modify `legacy/` or `db/`, and
must never edit a route weight. See `AGENTS.md`. `.cursor/hooks.json` enforces
that immutability with two fail-closed hooks backed by one script,
`.cursor/hooks/deny_protected_writes.py`: `preToolUse` denies `Write`/`Delete`
tool calls that touch `legacy/`, `db/`, or `strangler/routes.yaml`, and
`beforeShellExecution` denies shell commands that mutate those paths while still
allowing read-only commands (`cat`, `grep`, `git diff` ...) so agents can study
the monolith. The same hooks load in the IDE, in local SDK agents and in Cloud
Agents. The cutover controller writes `routes.yaml` through a plain subprocess
outside any agent, so promotion and rollback are unaffected.

Shared agent context lives next to the hooks and is picked up by every runtime:

- `AGENTS.md` — the non-negotiable rules.
- `.cursor/skills/extract-slice/SKILL.md` — the extraction playbook (service
  layout, parity contract, PR checklist) that agents load on demand.
- `.cursor/agents/*.md` — `extractor`, `parity-fixer` and a read-only
  `reviewer` subagent the main agent delegates to.

Cloud agents use `.cursor/Dockerfile`, which installs Docker Engine, Compose,
and the nested-container overlay and iptables compatibility layers needed to
run this stack. Cursor manages the workspace checkout; the image does not copy
the project into itself. The image and cloud execution are **unverified until a
Build runs in the user's Cursor workspace**.

When a candidate is ready, update its `upstream` in the relevant slice and let
the controller own `weight`. The route manifest is the source of truth, and
`strangler/render.py` produces the nginx configuration from it.

## Cleanup and tests

```sh
make down
make test
make lint
```

The browser pages are intentionally a manual check: after `make up`, visit the
storefront and migration console, change the candidate mode, run parity and
promotion commands, and watch the console's weight bar and request counts.

## Cloud migration orchestrator

The `orchestrator/` package is the Cursor SDK layer for the demo. It creates one
long-lived cloud Agent per slice, runs the authored `extract`, `parity_fix`, and
`cutover_plan` prompts in sequence, measures parity with `tools/parity.py`, and
persists resumable state plus every streamed Run event under `out/<slice>/`.
The comparator fetches each agent-reported branch, builds it in an isolated
verification worktree and Compose project, and copies the resulting report back
to the main checkout. Slices run concurrently and each slice owns its own cloud
PR. The agent never moves traffic weights: `tools/cutover.py` remains the only
controller for that.

```sh
CURSOR_API_KEY=... python -m orchestrator migrate \
  --slices catalog,orders,users,reports --wave-size 4
python -m orchestrator status
python -m orchestrator resume --slices catalog,orders,users,reports
```

`--runtime cloud` (default) gives every slice its own Cursor-hosted VM and an
auto-created PR. `--runtime local` creates the very same agent with
`LocalAgentOptions` instead: it works in a sandboxed git worktree under
`.work/<slice>/` on branch `migrate/<slice>`, loads the same `AGENTS.md`,
hooks, skill and subagents from the checkout, and pushes its branch for the
gate exactly like a cloud agent. Use it for offline development or when cloud
execution is unavailable; the phase machine, gate and cutover path are
identical. Once a slice passes the gate the controller runs
`tools/cutover.py register --no-reload` to point `strangler/routes.yaml` at the
candidate at weight 0; agents never edit that file. Registration only stages
the manifest: the façade is reloaded, and traffic promoted, after the slice PR
is merged and `make up` has started the candidate in the live Compose project.

`--notion off` is the default. `--notion api` writes deterministic per-phase
status using the Notion REST API; `--notion mcp` additionally runs the authored
Notion status phase through the official local stdio server (`npx -y
@notionhq/notion-mcp-server`). The cloud environment must provide `node`/`npx`;
`NOTION_TOKEN` is supplied to the cloud agent via its environment and MCP
configuration. Cloud stdio MCP is the expected path; the implementation retains
a local-runtime fallback if a future cloud sandbox cannot start it. Both Notion
modes require `NOTION_TOKEN` and `--notion-parent` (or
`NOTION_PARENT_PAGE_ID`).
