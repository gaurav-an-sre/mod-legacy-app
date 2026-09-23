# Sawan Mart · legacy modernization with the Cursor SDK — 20-minute demo

Audience: VP Engineering / Head of Platform at a retailer with a PHP monolith,
plus one skeptical staff engineer. Format: tell → show → tell. Every number on
screen comes from an artifact in this repo; nothing is mocked.

Pre-flight (do this 15 minutes before, not on stage):

```sh
make up && make seed                # legacy + candidate-catalog + façade + console
make seed-sawan                     # Thai Sawan Mart products into MySQL (ids 101+, db/ untouched)
SEARCH_MODE=enhanced docker compose up -d candidate-catalog   # candidate serves the enhanced ranker
make parity SLICE=catalog           # parity/catalog.json  -> 10/10
make search-eval SLICE=catalog      # search_eval/catalog.json -> 0.42 -> 1.00
python -m orchestrator status       # out/state.json has the catalog agent
open http://localhost:8080/_migration/
open http://localhost:8080/_migration/shop   # Sawan Mart storefront: legacy | candidate | façade
```

Keep two windows: the console (auto-refreshes every 3 s) and a terminal.
Have `out/catalog/cutover_plan.jsonl` and the catalog PR open in tabs.

---

## 0:00 – 3:00 · TELL — the problem Sawan Mart actually has

Sawan Mart (fictional; the numbers in this paragraph are illustrative framing)
runs 2,000 stores on a PHP/MySQL monolith that is 14 years old. Two problems,
one root cause:

1. **Search is bad in Thai.** `นํ้าปลา` (fish sauce with tone marks typed the
   "wrong" way) returns nothing; synonyms and category words return nothing.
   That is lost baskets on the most common queries, every day.
2. **Nobody dares change the monolith.** The last catalog change caused a
   6-hour outage on a payday weekend. Every fix is a quarter-long project with a
   change-freeze around it.

So the question isn't "can an LLM write a FastAPI service". Anyone can generate
code. The question is: *can we let agents modernize a live revenue system
without a human reviewing every line, and still sleep at night?*

That needs three things a chat window doesn't give you: **rules the agent
physically cannot break**, **gates that measure behaviour instead of trusting
prose**, and **a controller that owns production traffic**. That's what the
Cursor SDK gives us as a harness, and that's the demo.

## 3:00 – 5:00 · SHOW — the legacy system and the rules

Terminal:

```sh
curl 'localhost:8080/api/catalog/products?q=mug'        # the legacy API, served by PHP through the façade
make search-eval SLICE=catalog                          # legacy ranking on the Thai golden set: tone_marks 0.000, overall 0.421
```

Then the **Sawan Mart storefront** (`/_migration/shop`): type `โค้ก`. Legacy
column: 0 results. Candidate column: Coca-Cola, Pepsi. Façade column ("what
shoppers get now"): 0 results, badge *served by legacy* — the weight is 0. Try
`นํ้าปลา` (tone marks typed the wrong way): same picture. Leave this tab open;
it comes back in the cutover.

(`make seed-sawan` loads the same Thai product set the golden queries use,
from `search_eval/sawan_mart/`, into the live MySQL at ids 101+ without
touching `db/`; the legacy PHP and the candidate read the same rows.)

Open `AGENTS.md` (4 lines) and `.cursor/hooks.json`. Say:

> These are the rules of the house. Two directories are immutable — `legacy/`
> and `db/` — and the agent may *never* move a traffic weight. Those aren't
> instructions in a prompt. They're `preToolUse` and `beforeShellExecution`
> hooks, fail-closed, loaded identically by the IDE, by a local SDK agent and by
> a Cloud Agent. Same file, three runtimes.

Open `.cursor/skills/extract-slice/SKILL.md` and `.cursor/agents/` briefly:
the extraction playbook and the three subagents (`extractor`, `parity-fixer`,
read-only `reviewer`). One sentence: "the playbook lives in the repo, so the
IDE developer and the headless agent follow the same one."

## 5:00 – 9:00 · SHOW — the agent did the work (Cloud, asynchronously)

Console → **catalog** card → *Cursor Agent* section. Point at, in order:

- `runtime cloud` · agent id · **3 runs** — one durable agent per slice, three
  phases (`extract`, `parity_fix`, `cutover_plan`), resumed between phases.
  During development the cloud stream dropped mid-run; the controller
  reattached to the same agent (`Agent.resume(agent_id)`) and recovered the
  result instead of restarting. State is in `out/state.json`, not in a process.
- branch `cursor/extract-catalog-c537` · **PR link** — the Cloud Agent opened
  the PR itself (`auto_create_pr`). Click it: the diff is `services/catalog/`
  only. Zero lines in `legacy/`.
- **558 events persisted · 23 tool calls · read_file×15, grep_search×5** —
  every event of the run is on disk (`out/catalog/*.jsonl`). Expand
  "last 12 events". This is auditability, not a transcript we're paraphrasing.
- **$1.10 billed · 1.56M tokens (1.51M cache reads)** — server-side numbers
  from `agent.get_usage()`. The slice cost about a dollar. Say the line:
  "the CFO question has an answer, and we didn't estimate it."

Then the live moment. Terminal:

```sh
python -m orchestrator migrate --runtime local --slices catalog --starting-ref main
```

Let it stream for ~30 s: `[catalog/extract] tool read_file (completed)`,
worktree `.work/catalog`, branch `migrate/catalog`. Then Ctrl-C and say:

> Same `Agent`, same hooks, same skill — just `LocalAgentOptions` instead of a
> cloud VM. Cloud is for fleet fan-out with one PR per slice; local is the
> developer loop and the air-gapped fallback. The phase machine and the gate
> don't know the difference.

**Hook denial beat** (pre-recorded clip if the network is bad, otherwise live in
Cursor on `.work/catalog`): ask the agent to "add a debug line to
`legacy/index.php`". The hook returns a deny with the reason. Say nothing for
two seconds; let the room read it.

## 9:00 – 12:00 · SHOW — gates that measure, not trust

Console → catalog card header: **parity 100.0% (10/10)**. Terminal:

```sh
make parity SLICE=catalog
```

> The parity gate replays real requests against legacy and the candidate and
> diffs status *and* body byte-for-byte. Those ten include the ugly ones —
> `page=abc`, `per_page=3.7`, an Arabic-Indic digit as an id — because the
> monolith's quirks *are* the contract. Threshold is 0.99. The agent doesn't
> get to say "parity looks good"; the controller measures it. A parity-fixer
> subagent iterates until it passes, up to a bounded number of attempts.

Then scroll to *Sawan Mart search eval*: **legacy 42% → enhanced 100%**,
per-category table (tone_marks 0 → 100, synonyms 17 → 100, concept 33 → 100).

```sh
make search-eval SLICE=catalog
```

> 19 golden Thai queries, deterministic — no LLM in the loop at eval time. The
> same candidate service serves the legacy-identical behaviour for parity *and*
> the enhanced ranking behind `SEARCH_MODE=enhanced`. Lights stay on, search
> gets better, and both claims are measured.

## 12:00 – 16:00 · SHOW — production cutover the agent is not allowed to do

Terminal, with the console visible:

```sh
make cutover-demo SLICE=catalog
```

Narrate as the bar moves and the request counters change:

1. **health** — `/healthz` checks MySQL; a candidate that can't reach the DB
   returns 503 and is not promotable.
2. **parity gate** re-measured live — 10/10.
3. **register at weight 0, mirror on** — candidate sees shadow traffic, users
   see legacy. Console: `GATE READY`, 0 %.
4. **promote → 5 → 50 → 100** — each step soaks and compares candidate 5xx rate
   against legacy 5xx rate; console shows `RAMPING 50%` and the observed share
   converging on the weight.
   Switch to the storefront tab and re-run `โค้ก`: at 100 % the façade column
   now shows Coke and Pepsi with the badge *served by candidate* — shoppers just
   got the better search, and nobody edited nginx by hand.
5. **rollback** — one command, bar drops to 0, counters swing back to legacy;
   the storefront's façade column is back to 0 results, *served by legacy*.
6. **failure drill** — the script stops the candidate and tries to promote:
   `refusing promotion: candidate error rate 0.016 exceeds legacy 0.000`.

> Notice who did that: `tools/cutover.py`, a plain subprocess outside any
> agent. The agent's hook denies edits to `routes.yaml`; the controller is the
> only writer. The agent produces a *cutover plan* — soak windows, watch
> signals, rollback triggers — and a human runs it.

## 16:00 – 19:00 · TELL — what changed for Sawan Mart

Console KPI header as the backdrop.

| | Before | After (catalog slice) |
|---|---|---|
| Fish-sauce search with tone marks | 0 results | correct top-3 |
| Golden-query recall@3 | 42 % | 100 % |
| Legacy behaviour preserved | "we think so" | 10/10 replayed, 0.99 gate |
| Time to a reviewable PR | a quarter | one Cloud Agent session, same afternoon |
| Agent cost for the slice | — | $1.10 (billed, not estimated) |
| Who can move traffic | anyone with the YAML | the controller, gated |
| Rollback | redeploy | one command, seconds |

The operating model this unlocks: **one Cloud Agent per slice, fanned out in
waves** (`--wave-size 4`, orders/users/reports are the next three cards on the
console), each opening its own PR, each gated the same way, each costing about
a dollar. The team reviews PRs and cutover plans, not diffs of generated code.

Why Cursor specifically — say it plainly:

- Hooks, skills, subagents and `AGENTS.md` are **one set of files** shared by
  the IDE your developers already use, local SDK agents and Cloud Agents. The
  rule that stopped the agent on stage is the same rule that stops a developer's
  Tab completion from touching `legacy/`.
- **Cloud Agents** are durable objects: resumable across controller crashes,
  auto-PR, per-slice isolation, server-side usage/cost.
- The **same `Agent` API** runs locally when you can't ship code to a cloud VM.

## 19:00 – 20:00 · Close + questions

> We didn't ask an LLM to be careful. We built a harness where carelessness
> isn't possible, then let the agent be fast inside it. Next slice is `orders`;
> it has write routes, so the cutover plan will be harder — and that's the
> conversation I'd like to have with your platform team.

---

## Fallbacks

| Risk | Fallback |
|---|---|
| No network / Cursor API unreachable | Skip the live `--runtime local` run; everything else reads from disk. The console, parity, search-eval and cutover demo are fully offline. |
| Cloud stream drops mid-run (seen in dev) | Already handled: `stream_run` reattaches to the durable agent and asks for the final JSON. Mention it as a feature, don't hide it. |
| Docker slow to build | `make up` in pre-flight; never build on stage. |
| Hook denial needs Cursor IDE | 20-second pre-recorded clip of the deny in `.work/catalog`. |
| Question: "why Python, not TS?" | Same SDK surface; the harness (hooks/skills/agents) is language-neutral files in the repo. Offer to show the TS `Agent.create({cloud:{…}})` equivalent. |
| Question: "prompt caching / structured output?" | Cursor doesn't expose caching controls; cache reads *are* visible in `get_usage()` (1.51M of 1.56M tokens). Structured output is enforced by the controller's contract parser (`orchestrator/validation.py`), not by a schema flag. |

## Anticipated hard questions

- **"The parity fixtures are only 10 requests."** Yes — and they're the
  adversarial ones. Production replay from the façade log is the next step;
  the gate's shape doesn't change.
- **"What stops the agent from editing the hook itself?"** Nothing in this
  repo yet; in production the hook script and `AGENTS.md` are CODEOWNERS-
  protected and the Cloud Agent's PR can't merge without review. Good catch.
- **"Where's the human?"** Reviewing the PR, reading the cutover plan, and
  running `make promote`. The agent never touches traffic.
