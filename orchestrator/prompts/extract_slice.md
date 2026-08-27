# Extract the $slice slice out of the legacy monolith

You are modernizing one vertical slice of a legacy PHP/MySQL monolith into a containerized service,
using the strangler-fig pattern. Both systems must keep running side by side: nothing you do may take
the legacy application out of service, and no traffic shifts as a result of this run.

Slice: **$slice** — $slice_description

Legacy code lives in `$legacy_dir`. The legacy routes in scope are:

$legacy_routes

## Hard constraints

1. Do not modify anything under `$legacy_dir`. The monolith is the reference implementation and the
   rollback target; if it changes, parity means nothing. If you believe legacy code is wrong, record
   it in `notes` instead of fixing it.
2. Do not change the database schema, and do not migrate or copy data. The new service reads and
   writes the same MySQL tables as the monolith through `$db_dsn_env`. Owning the data comes later.
3. Register your routes in `strangler/routes.yaml` with `weight: 0`. Weight is moved by the cutover
   controller after the parity gate passes, never by you.
4. Preserve the legacy contract exactly, bug-for-bug: same status codes, same response body shape,
   same field names and types, same ordering, same behaviour on bad input. Where legacy emits
   something odd, reproduce it and note it. You are not improving behaviour in this run.

## Deliverables

- `$service_dir/` — the service, with a `Dockerfile` that builds and runs it, listening on
  `$container_port` and exposing `GET /healthz`.
- A compose entry so the service comes up alongside the monolith and MySQL.
- Tests that assert the legacy contract for every route in scope, including the failure cases you
  found in the legacy code (missing parameters, unknown ids, empty results).
- The `weight: 0` entries in `strangler/routes.yaml`.

## Before you answer

Push your work: your branch is what the migration is verified against. The orchestrator re-runs the
parity harness itself, from your branch, on its own machine — your reported rate is a claim, not the
gate. Make sure everything needed to build and run your service is committed.

Read the legacy code for these routes first and derive the contract from what it actually does —
not from what the route names suggest. Then build the image, bring the stack up, and run the parity
harness with `$parity_cmd`. Iterate until it passes or until the only differences left are ones you
can explain. A slice that has never been run against the monolith is not finished.

## Reply

Reply with strict JSON and nothing else:

{"slice": "$slice", "service_name": "<compose service name>", "container_port": $container_port,
 "branch": "<the git branch your work is pushed on>",
 "routes": ["<path pattern>", ...], "parity_match_rate": <float 0..1>,
 "unresolved_differences": [{"route": "<path>", "why": "<one line>"}],
 "notes": ["<legacy quirk you reproduced deliberately>", ...]}
