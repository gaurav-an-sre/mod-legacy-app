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

`tools/parity.py` replays `traffic/requests.yaml`, compares status and normalized
JSON/text bodies, and writes a self-explanatory result for every request to
`parity/<slice>.json`. Normalization removes deterministic volatile keys such
as IDs and timestamps. The promotion gate is decided by this replay comparator,
not by diffing mirrored responses. Mirroring exercises a candidate with real
load before it receives real users; it is not the parity decision.

The controller owns the weights `[0, 5, 50, 100]` in `strangler/routes.yaml`.
`tools/cutover.py` requires the latest parity report to meet the threshold
(default `0.99`) and requires the candidate access-log error rate to be no
greater than legacy's over the available soak log. It rewrites the nginx
configuration and runs `nginx -s reload` through Compose, rather than
restarting the façade.

## Cursor agent handoff

`services/` intentionally contains only a README. Extraction agents should
write modernized services there, but must never modify `legacy/` or `db/`, and
must never edit a route weight. See `AGENTS.md`. The façade's
`.cursor/hooks.json` policy protects evidence paths locally.

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
