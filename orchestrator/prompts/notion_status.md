# Write the migration record for the $slice slice into Notion

The Notion MCP server is available. Maintain the migration record for the **$slice** slice as a page
under the parent page with id $parent_page_id.

A page for this slice may already exist — search the parent for one titled with this slice name
before creating anything, and update it in place if you find it. One page per slice, for the whole
migration; do not create a second page per phase.

The facts come from these files and nowhere else. Do not restate them from memory, and do not
re-derive or round any number:

- extraction result: $extraction_json_path
- latest parity report: $parity_report_path
- cutover plan (may be absent): $cutover_plan_path
- current route weights: $routes_yaml_path

The page must let someone who was not involved answer, in under a minute: what has moved, how
confident we are, and how to undo it. Structure it as:

1. **Status line** — slice, current traffic weight, parity rate, and whether the gate is passing.
2. **What was extracted** — the routes now served by the candidate, its container, and the fact that
   it still shares the monolith's database.
3. **Parity** — the match rate, the differences deliberately accepted as benign with their reasons,
   and anything still unresolved. If there are unresolved differences, say so plainly at the top
   rather than burying it here.
4. **Legacy quirks reproduced on purpose** — the bug-for-bug behaviours carried over, so nobody
   "fixes" them later without knowing they were deliberate.
5. **Cutover** — the ramp steps and their stop conditions, and the rollback trigger, as an unchecked
   to_do list so an operator can work through it.

Write only what those files support. If a file is missing, say the phase has not run yet rather than
inventing its content — a migration record that overstates progress is worse than no record.

## Reply

Reply with strict JSON and nothing else:

{"slice": "$slice", "page_id": "<id>", "page_url": "<url>", "created": <true if new, false if updated>}
