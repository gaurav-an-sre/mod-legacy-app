---
name: extract-slice
description: Strangler-fig extraction playbook for this repo. Use whenever asked to extract, modernize, or fix parity for a slice (catalog, orders, users, reports) of the legacy PHP/MySQL monolith into services/.
---

# Extract a slice out of the legacy monolith

The monolith in `legacy/` keeps serving production behind the nginx façade in
`strangler/`. You build a candidate service for one slice, prove it is
bug-for-bug identical with the parity harness, and hand the result back on a
branch. Nothing you do shifts traffic.

## Contract you must honour

1. `legacy/` and `db/` are immutable; `strangler/routes.yaml` belongs to the
   cutover controller. Hooks in `.cursor/hooks.json` deny writes there; do not
   work around them. If legacy code is wrong, reproduce the bug and record it
   in your `notes`.
2. Same MySQL tables, same `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`
   environment variables as the monolith. No schema changes, no data copy.
3. Same status codes, body shape, field names/types, ordering and bad-input
   behaviour as legacy. Derive the contract from `legacy/index.php`, not from
   the route names.

## Service layout

```
services/<slice>/
  Dockerfile          # builds and runs the service on the container port
  app.py              # FastAPI app; GET /healthz returns {"status":"ok"}
  requirements.txt
  tests/test_contract.py
```

Add a compose service named `candidate-<slice>` to `compose.yaml`
(`build: ./services/<slice>`, same `DB_*` env as `legacy`, `depends_on: db`).

## Prove it

```sh
make up                       # builds the stack including your service
make seed
make parity SLICE=<slice>     # writes parity/<slice>.json, exits 1 below threshold
```

`make parity` runs from the `parity` compose profile; pass your service with
`CANDIDATE_URL=http://candidate-<slice>:<port>` if it differs from the default.
Read `parity/<slice>.json` and fix every `diff` you cannot explain. A candidate
that has never been replayed against the monolith is not finished.

Run the parity loop through the `parity-fixer` subagent and ask the `reviewer`
subagent (read-only) to check the branch before you reply.

## Hand back

Commit and push everything needed to build and run the service. Reply with the
strict JSON block requested in your prompt: the orchestrator re-measures parity
from your branch on its own machine, registers your candidate in
`strangler/routes.yaml`, and owns every weight change after that.
