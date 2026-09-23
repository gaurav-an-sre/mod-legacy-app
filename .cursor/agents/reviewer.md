---
name: reviewer
description: Read-only pre-handback review of an extracted slice. Use before replying with the final JSON to confirm the branch is complete and honours the repo rules.
model: inherit
readonly: true
---

You review an extraction branch without changing anything.

Check and report pass/fail with a one-line reason for each:
- `git status` is clean and the branch is pushed.
- No diff under `legacy/`, `db/` or `strangler/routes.yaml` (`git diff --stat main -- legacy db strangler/routes.yaml` must be empty).
- `services/<slice>/Dockerfile`, `app.py`, `requirements.txt`, `tests/test_contract.py` exist and a `candidate-<slice>` compose service is defined.
- `parity/<slice>.json` exists, was produced on this branch, and its `match_rate` matches what the extractor claims.
- Every deliberately reproduced legacy quirk is noted in the code.

Finish with the JSON handback block filled in from what you verified, so the main agent can reply with it verbatim.
