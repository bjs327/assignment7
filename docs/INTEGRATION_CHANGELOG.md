# Integration change log

Every significant change made during end-to-end integration, newest last. Entries record what
actually happened, with the commands run and their real output. Branch: `integration/end-to-end`
(local, from `origin/main` at `5b36b53`). Nothing in this log has been committed or pushed unless
an entry says so.

---

## 2026-10-27 00:26 MST: Baseline audit (no code changes)

- **Component:** whole repository
- **Files modified:** none in the codebase. Added `docs/INTEGRATION_AUDIT.md` and this file.
- **Actions:** fetched `origin`; confirmed every remote branch is merged into `main`; created
  local branch `integration/end-to-end` from `origin/main`.
- **Environment fix (local only, not in git):** reinstalled `pydantic_core==2.50.1` and
  `cryptography==48.0.1` in `.venv`, whose compiled extensions were missing.
- **Tests executed:** `python -m pytest -q`
- **Actual result:** 752 passed, 2 failed (`tests/orchestrator/test_graph.py` loop-guard tests,
  classified as outdated in the audit), 0 skipped.
- **Status:** baseline recorded; integration plan awaiting Jordan's approval.

---

## 2026-10-27 00:34 MST: Intake outputs dropped at the subgraph boundary (audit A1)

- **Component:** shared state (Verena), Intake (Jordan)
- **Files modified:** `src/state.py`, `tests/test_intake_integration.py`
- **Original problem:** in the full graph, Intake's `intake_requests` (its record of every field
  another agent asked for) and `intake_message` (what the taxpayer was told when the interview
  stopped for a person) disappeared after every Intake run.
- **Root cause:** Intake runs as a subgraph. LangGraph only copies a subgraph's outputs back
  into the parent state for keys the parent schema declares, and `SilverFileState` didn't
  declare these two. `intake_status` also lacked `"awaiting_human"` in its `Literal`
  (documentation only; not enforced at runtime).
- **Changes made:** declared `intake_requests: dict[str, dict[str, Any]]` and
  `intake_message: str` in `SilverFileState`; added `"awaiting_human"` to `intake_status`;
  updated the "who writes what" docstring. No reducers added: Intake writes both keys whole.
- **Before:** the request count restarted at 1 on every Intake visit, so the
  `repeated_request` guard (`MAX_REQUESTS_PER_FIELD = 3`) could never fire in the full graph,
  and the hold message never reached anything outside Intake.
- **After:** both keys persist in the checkpoint. A field requested again and again now reaches
  a preparer as a `repeated_request` flag.
- **Impact on other agents:** additive; no agent reads these keys except Intake and (later) the
  dashboard.
- **Tests added:** `test_intake_message_reaches_the_main_state`,
  `test_intake_requests_reach_the_main_state`,
  `test_a_field_requested_again_and_again_reaches_a_preparer_in_the_full_graph`.
- **Verification:** with `src/state.py` reverted to `HEAD`, `python -m pytest
  tests/test_intake_integration.py` gave 3 failed, 26 passed (exactly the three new tests); with
  the fix, 29 passed.
- **Required team review:** **REQUIRES OWNER REVIEW (Verena, `src/state.py`).**
- **Status:** done locally, uncommitted.

## 2026-10-27 00:34 MST: Loop-guard tests encoded old Intake behaviour (audit A2)

- **Component:** Orchestrator tests (Verena)
- **Files modified:** `tests/orchestrator/test_graph.py`
- **Original problem:** `test_loop_guard_breaks_the_intake_eligibility_cycle` and
  `test_loop_guard_counts_again_after_a_person_steps_in` failed on `main`.
- **Root cause:** they produced a cycle by relying on Intake ignoring Eligibility's extra
  questions. Commit `06914b5` made Intake ask them, so with real agents the run now completes
  (verified: approved after 4 extra questions). Probes for another natural cycle (W-2 box 1 or
  box 2 blank) found none: box 1 escalates at once, and box 2 blank runs to approval.
- **Changes made:** the tests now patch `graph.eligibility_node` (pytest `monkeypatch`, test-only)
  with a faulty Eligibility & Rules that returns `needs_facts` but names no fact. Intake has
  nothing to ask and finishes, so `intake -> extraction -> eligibility_rules` repeats: the same
  pathological cycle as before. **Assertions unchanged** (exact route, issue title, options,
  fresh count after a person steps in). No production code changed.
- **Tests executed:** `python -m pytest tests/orchestrator` gave 29 passed.
- **Required team review:** **REQUIRES OWNER REVIEW (Verena).**
- **Status:** done locally, uncommitted.

## 2026-10-27 00:34 MST: Full suite after A1 and A2

- **Command:** `python -m pytest -q`
- **Result:** 757 passed, 0 failed, 0 skipped (752 baseline + 2 repaired + 3 new).
- **Incident, no data lost:** a `git stash` / `git stash pop` used for the before/after check
  restored every working file but left `.git/index` empty (every tracked file showed as
  deleted plus untracked). Repaired with `git reset` (index only; working tree untouched), then
  confirmed `git status` showed only the intended changes. The before/after check was redone by
  swapping `src/state.py` with `git show HEAD:src/state.py` instead.

---

## 2026-10-27 00:36-01:05 MST: Jordan's decisions on the plan

Jordan approved: editing Verena's `state.py` and tests (labelled REQUIRES OWNER REVIEW); a
Streamlit dashboard; typed-in form values collected by a non-agent entry form, not by Intake.
The assignment text (specification) was supplied and checked against the repo (see the
"Specification check" section of `docs/INTEGRATION_AUDIT.md`).

## 2026-10-27 ~00:40 MST: Document registration (audit A3, A4)

- **Component:** new, `src/app/` (no owner assigned; written by Jordan)
- **Files:** `src/app/__init__.py`, `src/app/documents.py`, `.gitignore` (`data/uploads/`, `out/`)
- **Original problem:** nothing wrote `documents` (the files Extraction reads) or
  `entered_documents` / `entered_amounts` (SSA-1099, 1099-INT, 1099-DIV, 1099-R boxes and
  Schedule 1-3 lines). Tests injected them by hand.
- **Changes:** `register_upload` / `register_path`: accepts JPG, PNG, WEBP (the types
  Extraction's `load_image` reads), up to 15 MB, verifies it opens as an image, refuses a
  duplicate by SHA-256, assigns `w2-1`, `w2-2`, ..., saves as `data/uploads/<session>/<id><ext>`
  (the taxpayer's own file name is not kept: it can contain their name). `enter_document` /
  `enter_amount`: validate form type, known boxes, money format (no rounding, no arithmetic) and
  record `entered_by` / `entered_at` (spec 4.2 "direct user input").
- **Separation of duties:** Intake still records only the inventory; registration never
  interprets a value; Extraction reads the images; Computation does the arithmetic.
- **Impact:** extra keys `entered_by`/`entered_at`/`source` on `entered_documents` entries are
  ignored by Eligibility's `DocumentFacts` (pydantic default) and Computation.
- **Tests:** `tests/app/test_documents.py` (18). Result: 18 passed.
- **Status:** done locally, uncommitted. Needs an owner (see team summary).

## 2026-10-27 ~00:42 MST: Session runner and terminal program (audit A5)

- **Files:** `src/app/session.py`, `src/app/cli.py`, `run.py` (new `session` and `dashboard`
  commands; `session` is dispatched before argparse because `REMAINDER` won't take `--options`)
- **Behaviour:** one driver for terminal and dashboard. States read from the checkpoint only
  (`not_started`, `question`, `preparer`, `finished`); answers carry `question_id`, and a stale
  one is ignored; starting an existing session again changes nothing.
- **Defect found while testing:** the resume summary showed "(not answered)" for answered
  questions. Root cause: mid-interview answers live in the Intake subgraph's checkpoint, not the
  parent state. Fix: read `graph.get_state(config, subgraphs=True)`.
- **Performance:** the full run took ~38 s because Review's default MCP caller starts a stdio
  server per tool call. `open_graph` passes Brendan's in-process MCP client (`mcp_tool_caller`,
  same server and schema validation) as `review_caller`: 7 eval tests went from 128 s to 8 s.
  Review's own code is unchanged; `python run.py review` still uses the stdio caller (~45 s).
- **Tests:** `tests/app/test_eval_cases.py`. Status: done locally, uncommitted.

## 2026-10-27 ~00:45 MST: Draft Form 1040-SR with provenance (audit A6, spec 2.1, 4.2)

- **Files:** `src/app/form1040sr.py`
- **Behaviour:** `build_form(final_package)` lays out every spec 3.1 line (plus the lines
  between), filing status, SSNs, dependents, 12d boxes, Review's verdict, human decisions and
  open issues. `render_html` makes one self-contained page: hover an amount for its summary,
  click it for tool, inputs, working, supporting calls, source documents (W-2: model, time, file
  hash, raw text printed; typed forms: who and when) and rule citations resolved from
  `data/policy.md`. Presentation only: copies values, never computes.
- **Verification:** every line of eval case 1 equals the hand-worked `MFJ_EXPECTED` in
  `tests/computation/scenarios.py`; every amount has a tool and citations.

## 2026-10-27 ~00:50 MST: Streamlit dashboard (spec 2.1 "superuser dashboard")

- **Files:** `src/app/dashboard.py`, `requirements.txt` (`streamlit>=1.40`), `run.py`
- **Behaviour:** Taxpayer view (upload, typed forms, interview, welcome-back summary, result);
  Kushim preparer view (all sessions, review packet with per-issue decisions through the
  Orchestrator's `submit_decisions`, draft form, audit record). No login (documented limitation).
- **Defects found while testing:** (1) Streamlit's first-run e-mail prompt blocked startup when
  launched non-interactively: now started with `--server.headless true
  --browser.gatherUsageStats false` and the URL printed. (2) The offline "add demo W-2s" button
  added both images, so an inventory of "a W-2" correctly escalated as a document conflict: now
  one button per image.
- **Tests:** `tests/app/test_dashboard.py` (3, Streamlit `AppTest`, headless). Real server
  checked: `python run.py dashboard --offline --port 8599`, `/_stcore/health` returned `ok`.

## 2026-10-27 ~00:55 MST: Intake fixes (Jordan's component)

- **Files:** `src/agents/intake.py`, `tests/test_intake.py`, `tests/test_intake_integration.py`
1. **Tax advice not detected (spec 2, 4.4 condition 4).** Two of the specification's three
   example questions, "Should I convert my IRA to a Roth?" and "Would I pay less if we filed
   separately?", raised no flag: verified in the full graph, the run went straight to
   `approved`. Root cause: `TAX_ADVICE_PATTERNS` had no pattern for these phrasings. Added four
   patterns; checked no false positives on ordinary answers, including "I have a 1099-R from my
   Roth IRA" (now a test). Before: 4 of the new cases failed; after: pass. In the full graph the
   run now pauses with "Tax advice requested" (options: Noted, Stop).
2. **Progress said "13 of 13 answered" while asking a 14th question** requested by Eligibility:
   `_progress` now counts requested questions. Test fails before, passes after.
3. **Summary of a half-finished interview said "Dependents: none"** for unanswered questions:
   now "(not answered)". Test fails before, passes after.
4. **Honest uncertainty (spec 4.3):** "not sure" on a yes/no question now also tells the
   taxpayer they can say "talk to a person" to be flagged for a volunteer.

## 2026-10-27 ~01:00 MST: A rejected real SSN was saved in the SQLite file (security)

- **Component:** Intake (Jordan) and `src/app/session.py`
- **Original problem:** typing a non-test SSN (e.g. `123-45-6789`) got "I haven't saved it",
  but the SQLite `writes` table held it (2 rows, as LangGraph's `__resume__` value). Verified
  for both the full graph and Intake's standalone graph.
- **Root cause:** LangGraph saves the resume value before any node runs; Intake's SSN guard ran
  inside the node.
- **Fix:** new `intake.ssn_refusal(text)`; both drivers (`intake.submit_answer`,
  `app.session.answer`) refuse such an answer before calling the graph. Intake's in-graph check
  stays as a second line (now uses the same function).
- **Tests:** `test_a_real_looking_ssn_never_reaches_the_saved_session`,
  `test_the_standalone_interview_refuses_it_before_the_graph_too`: they query every table
  through SQLite (a raw read of the file missed rows still in the WAL, which at first made the
  standalone test pass without the fix; corrected). Without the fix: 2 failed; with: 2 passed.
- **Remaining risk:** code that calls `graph.invoke(Command(resume=...))` directly bypasses the
  screen (tests do). Preparer notes in human review are not screened. Any SQLite file used
  before this fix (e.g. a local `data/sessions.sqlite`) may contain such writes: delete it.

## 2026-10-27 ~01:02 MST: Eval cases, README (audit A7, A9)

- **Files:** `demo/eval_case_1.md`, `demo/eval_case_2.md`, `demo/eval_case_1_forms.json`,
  `demo/eval_case_1_answers.txt`, `demo/eval_case_2_answers.txt`, `evals/cases.json`, `README.md`
- Case 1: clean run to `approved` with the hand-worked line values. Case 2: spouse died in 2025
  → "Filing status unclear" (FS-02, FS-06, ESC-05) → preparer chooses MFJ → `approved`.
  Every documented HITL trigger in `eval_case_2.md` was run before being written down; the
  tax-advice one exposed defect 1 above.
- Case 2 also run entirely from the terminal: `run.py session` (pause) → `run.py review` with
  inputs `Preparer Pat`, `1`, `2`, note → `run.py session` again → `approved`, form written.
- README: statuses, setup note about the damaged-venv error, run commands, Mermaid diagram
  generated from the compiled graph, layout. **Orchestration paragraph and status rows of other
  owners need their review.**

## 2026-10-27 01:05 MST: Full suite

- **Command:** `python -m pytest -q`
- **Result:** 796 passed, 0 failed, 0 skipped.

## 2026-10-27 01:10 MST: Integration documents

- Added `docs/INTEGRATION_TEST_REPORT.md` and `docs/TEAM_REVIEW_SUMMARY.md`; wrote the Intake
  section and a `src/app/` section in `docs/IMPLEMENTATION_NOTES.md` (the Orchestrator section is
  untouched; a correction to it is proposed in the team summary).
- **Status of the whole integration:** done locally, uncommitted, awaiting owner reviews listed
  in `docs/TEAM_REVIEW_SUMMARY.md`.
