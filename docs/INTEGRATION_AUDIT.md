# SilverFile integration audit (Phase 1 baseline)

Date: 2026-10-27 00:26 MST. Auditor: Jordan (with Claude Code). Status: **baseline, no code changed yet.**

## Git state

| Item | Value |
|---|---|
| Baseline commit | `5b36b53` (`origin/main`, "Merge pull request #6 from bjs327/jordan-intake-agent") |
| Integration branch | `integration/end-to-end`, created locally from `origin/main`; not pushed |
| Working tree | clean; nothing uncommitted was touched |
| Remote branches | `david/eligibility-rules`, `marco/review-audit`, `yassine/ai-disclosure`, `yassine/extraction-security`, `jordan-intake-agent`: all fully merged into `main` (0 commits ahead) |
| Local `jordan-intake-agent` | 8 commits behind `main`, nothing unmerged |
| Untracked local data | `data/sessions.sqlite` (ignored by `*.sqlite` in `.gitignore`, correctly kept out of git) |

## Environment

- Python 3.12.10 in `.venv` (README says 3.11+).
- **The venv was broken before any test could run:** the compiled extensions for `pydantic_core`
  and `cryptography` were missing (`ImportError: cannot import name 'ArgsKwargs'`), so pytest
  crashed at startup. Fixed locally with `pip install --force-reinstall --no-deps
  pydantic_core==2.50.1 cryptography==48.0.1`. This is a local environment problem, not a repo defect.
  Teammates who see the same error should recreate their venv.

## Baseline test results

Command: `python -m pytest -q` (from the repo root, venv active)

| Passed | Failed | Skipped |
|---|---|---|
| 752 | 2 | 0 |

Failures (both `tests/orchestrator/test_graph.py`, owner Verena):

- `test_loop_guard_breaks_the_intake_eligibility_cycle`
- `test_loop_guard_counts_again_after_a_person_steps_in`

**Classification: outdated tests, not defects.** Both tests produce the cycle by having Eligibility
ask for facts Intake "can't ask yet". Commit `06914b5` (Intake) made Intake ask those questions,
so the cycle no longer happens: the run now stops at Intake's first extra question
(`route = intake, extraction, eligibility_rules, intake`). The safety requirement they protect
(the loop guard catches a real hand-off cycle and pauses for a person) is still valid and needs a
scenario that still cycles. The commit message flagged this for the Orchestrator owner.

Lint: the repo has no ruff/flake8 config. A user-global ruff config reports 71 style findings
(import order, `datetime.UTC`, etc.), none of which are correctness issues. Not addressed:
fixing them would touch every teammate's files for no behavioural gain.

## What works (verified)

Probe script: full graph, offline W-2 reader, real `tax_math` through MCP in-process.

- Full pipeline Intake → Extraction (parallel W-2 branches) → Eligibility → Computation → Review
  → finalize reaches `run_status = "approved"` with a `draft_return` and `final_package`
  (`test_clean_session_runs_to_an_approved_draft`).
- Eligibility's extra questions reach the taxpayer and come back: with `eligibility_facts`
  removed, Eligibility returns `needs_facts`, the Orchestrator maps `eligibility_reask` onto
  `intake_reask`, Intake asks `spouse_died_in_2025`, `can_be_claimed_as_dependent`,
  `dual_status_alien`, `spouse_can_be_claimed_as_dependent`, then confirm, and the run finishes
  `approved`. The answers are in `intake_answers` (which Eligibility reads through
  `facts_from_state`) and in `intake_record.additional_facts`.
- Review loop bounded at 3; human review pause/resume; SQLite resume after restart; SSN
  allow-list and redaction; deterministic `tax_math` via MCP. All covered by passing tests.

## What is broken or incomplete

| # | Issue | Type | Agents affected | Priority / risk | Owner |
|---|---|---|---|---|---|
| A1 | `SilverFileState` lacks `intake_requests` and `intake_message`, and `intake_status` doesn't list `"awaiting_human"`. LangGraph drops undeclared subgraph outputs at the boundary, so in the full graph **Intake's request log resets on every visit**: the `repeated_request` guard (`MAX_REQUESTS_PER_FIELD`) can never trigger, and the message shown to the taxpayer on a hold is lost. Verified by probe: `intake_requests` absent from parent state after Intake ran. | Defect (integration) | Intake, Orchestrator, state | High / low risk (additive) | Verena (state.py), Jordan |
| A2 | Two Orchestrator loop-guard tests encode the old "Intake drops requests" behaviour (see above). | Outdated tests | Orchestrator | High (red suite) / low risk | Verena |
| A3 | No document registration. `documents` (the uploaded files Extraction reads) is "read by agents but not written by any agent". Tests inject it by hand. Intake records only the inventory (types, counts). There's no way to register an upload with an ID, type and safe file reference. | Missing feature | Extraction, Intake, Orchestrator | High / medium | **Unassigned** (upload interface) |
| A4 | Non-W-2 values (`entered_documents` boxes for SSA-1099, 1099-INT, 1099-R, 1099-DIV; `entered_amounts`) are collected by nobody. Computation's notes list them as "Inputs nobody collects yet". Tests use a fixture (`mfj_return()`). | Missing feature | Computation, Eligibility | High / medium | **Unassigned** (dashboard / hand entry) |
| A5 | No command runs the whole system. `run.py` has `intake`, `review`, `extract`, `eligibility`, `tax-math-server`, but no end-to-end session command. | Missing feature | All | High / low | Unassigned (MLOps/DevOps is TODO in README) |
| A6 | No human-readable return. `final_package.draft_return` is structured JSON only. No dashboard exists, although several docs refer to one. | Missing feature | Output | Medium (depends on spec) | **Unassigned** |
| A7 | Eval cases empty: `evals/cases.json`, `demo/eval_case_1.md`, `demo/eval_case_2.md` are TODO. | Missing deliverable | All | High / low | Unassigned |
| A8 | `docs/metrics.md` (context-window strategy, spec 4.5) is TODO, and `run.py` has no `--no-context-strategy` batch run. | Missing deliverable | Memory | Medium | Jordan (memory) |
| A9 | README is out of date (Orchestrator and Intake "Placeholder", `state.py`/`graph.py`/`memory.py` "placeholder", no orchestration diagram). Intake section of IMPLEMENTATION_NOTES is TODO. | Docs | All | Medium / none | Each owner; Jordan for Intake |
| A10 | After Intake re-runs for extra questions, the Orchestrator marks Extraction stale and re-reads every W-2. Correct but wasteful: with the vision model it repeats paid API calls for unchanged files. | Inefficiency | Orchestrator, Extraction | Low | Verena |
| A11 | The assignment specification itself is not in the repo, though code and docs cite its sections (3.1-3.3, 4.1-4.5, 5.2). Requirements for the dashboard, output format and eval cases can't be checked against it. | **Blocker for Phases 4, 7, 8** | All | High | Jordan (to provide) |

## Separation-of-duties check (no violations found)

- Intake: no arithmetic, no rule interpretation, no document reading (estimated payments kept one by one; DOB kept as a date).
- Orchestrator: reads status fields only, never amounts.
- Computation: every amount from a `tax_math` MCP tool, with arguments recorded for replay.
- Eligibility: deterministic rules engine citing `data/policy.md` rule IDs.

## Specification check (added 2026-10-27, after Jordan supplied the assignment text)

Requirements from the specification that the grader exercises (rubric D), against the repo:

| Spec | Requirement | Repo today | Gap |
|---|---|---|---|
| 2.1, D | "A filled out form": a completed draft 1040-SR | `final_package.draft_return` (JSON lines with provenance) | **No human-readable form.** Missing. |
| 4.2 | Click or hover a number to see its calculation and sources; "direct user input" with date and who entered it | Provenance is in every `draft_return` line (tool, arguments, working, rule IDs, sources; `entered_by`/`entered_at` for typed values) | Data exists, **no display**. |
| 2.1, D | "Superuser" dashboard for Kushim employees | None. HITL decisions only via `python run.py review` (terminal) | **Missing.** |
| D | Grader fills in a clean run with their own made-up data, uploads a W-2 image | Intake CLI exists; no upload; no end-to-end session command; other forms only via test fixtures | **Missing:** one entry point that runs interview, W-2 upload, form-value entry and the graph. |
| 3.1, 3.3 | SSA-1099, 1099-INT, 1099-DIV, 1099-R and Schedules 1-3 values "just entered directly" | Computation/Eligibility read `entered_documents` and `entered_amounts` | **No collector.** |
| D | W-2 amounts read from an image | Vision extractor (needs `ANTHROPIC_API_KEY`), offline fixture reader for tests | Works; only demo W-2s have offline ground truth. |
| 4.1 | Diagram in README; loop guard test that triggers | No diagram; loop-guard tests red (A2) | Missing / outdated. |
| 4.3 | One question at a time, plain language, drift, PII, resumability with summary, honest uncertainty ("not sure" detection exists) | Implemented in Intake | Report section (IMPLEMENTATION_NOTES Intake) is TODO. |
| 4.4 | Interrupt-based HITL for the six conditions | Implemented across Intake, Eligibility, Extraction, Orchestrator human review | No eval case demonstrating it end to end. |
| 4.5 | `--no-context-strategy` batch of 10 items, per-item input tokens in `docs/metrics.md` | `memory.build_context` / `estimate_tokens` exist | **No batch run, no numbers.** |
| 5.2 | `demo/eval_case_1.md`, `eval_case_2.md` with data that produce a working run, with HITL resolution steps | TODO | Missing. |
| 5.1 | `docs/graph.png`, `traces/`, demo video | Missing | graph.png can be generated; the video must be recorded by the team. |
