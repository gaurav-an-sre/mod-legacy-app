# Close the parity gaps for the $slice slice

Mirrored production-shaped traffic was replayed against both the legacy monolith and your extracted
service, and their responses were diffed. The report is at `$parity_report_path`; the current match
rate is $parity_match_rate and the gate requires $parity_threshold.

Traffic cannot move to your service until these differences are gone, so this run exists to close
them. Read the report first — each entry records the request, the legacy response, and yours.

## Rules

1. Legacy is right by definition here, even where it is ugly. Match its output; do not correct it.
   The one exception is a difference that is inherently non-deterministic (timestamps, generated
   ids, ordering the legacy code itself does not fix); those go in `benign` with the reason.
2. Do not modify anything under `$legacy_dir`, do not touch the database schema, and do not change
   any route weight.
3. For each real difference, add a test that fails before your fix and passes after it. A fix
   without a test is how the same divergence comes back at 50% traffic.
4. Rebuild the image, bring the stack up, and re-run `$parity_cmd`. Report the rate you actually
   measured on the last run, not the rate you expect.
5. Commit and push everything. The orchestrator re-runs the harness from your branch on its own
   machine, and that measurement — not the rate you report — is what moves traffic.

## Reply

Reply with strict JSON and nothing else:

{"slice": "$slice", "parity_match_rate": <float 0..1>,
 "branch": "<the git branch your work is pushed on>",
 "fixed": [{"route": "<path>", "cause": "<why the two differed>", "fix": "<what you changed>"}],
 "benign": [{"route": "<path>", "why": "<why this difference cannot be eliminated>"}],
 "still_failing": [{"route": "<path>", "why": "<one line>"}]}
