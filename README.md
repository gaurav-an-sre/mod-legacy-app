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

The candidate in `tests/fixtures/fake_candidate/` is not an extracted service.
It is a tiny fixture used to make the platform verifiable without a Cursor
agent. It deliberately returns a differently rounded price by default.

```sh
# The candidate is divergent, so this writes parity/catalog.json and exits 1.
make parity

# Point the fixture at matching responses, then replay.
FAKE_DIVERGE=0 docker compose up -d --build fake-candidate
make parity

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

## Cursor agent handoff

`services/` intentionally contains only a README. Extraction agents should
write modernized services there, but must never modify `legacy/` or `db/`, and
must never edit a route weight. See `AGENTS.md`. The local `.cursor/hooks.json`
policy enforces that immutability: agent write, edit, and delete tools are
blocked on `legacy/`, `db/`, and `strangler/routes.yaml`. Shell commands are not
blocked, because extraction agents must be able to read the monolith and run
`make seed`; the cutover controller writes `routes.yaml` through a plain
subprocess, so promotion and rollback are unaffected.

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

`--notion off` is the default. `--notion api` writes deterministic per-phase
status using the Notion REST API; `--notion mcp` additionally runs the authored
Notion status phase through the official local stdio server (`npx -y
@notionhq/notion-mcp-server`). The cloud environment must provide `node`/`npx`;
`NOTION_TOKEN` is supplied to the cloud agent via its environment and MCP
configuration. Cloud stdio MCP is the expected path; the implementation retains
a local-runtime fallback if a future cloud sandbox cannot start it. Both Notion
modes require `NOTION_TOKEN` and `--notion-parent` (or
`NOTION_PARENT_PAGE_ID`).
