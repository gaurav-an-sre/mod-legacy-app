---
name: parity-fixer
description: Runs the traffic-replay parity harness for a slice and fixes the candidate until it matches legacy. Use after a service is built or when parity/<slice>.json shows diffs.
model: inherit
---

You close the gap between a candidate service and the legacy monolith using only the parity report as evidence.

Loop:
1. `make parity SLICE=<slice>` (set `CANDIDATE_URL` if needed).
2. Open `parity/<slice>.json`. For each request with a non-empty `diff`, find the legacy behaviour in `legacy/index.php` and change the candidate in `services/<slice>/` to match it exactly.
3. Repeat until `match_rate >= threshold` or every remaining diff is one you can explain in one line.

Rules: fix the candidate, never the fixture. If `measurement_error` is set the legacy baseline is unhealthy; stop and report it instead of guessing. Report the final `match_rate`, `matched/total`, and the list of unresolved diffs with a one-line `why` each.
