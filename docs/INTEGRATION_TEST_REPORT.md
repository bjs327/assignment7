# Integration test report

## 1. Run

| Item | Value |
|---|---|
| Date | 2026-10-27 01:06 MST |
| Branch | `integration/end-to-end` (local, not pushed) |
| Base commit | `5b36b53` (`origin/main`); all integration changes are uncommitted on top of it |
| Tester | Jordan, with Claude Code |

## 2. Environment

macOS 13.7.8 (x86_64), Python 3.12.10 in `.venv`. langgraph 1.2.17, langgraph-checkpoint-sqlite
3.1.2, mcp 2.2.0, pydantic 2.14.1, pytest 9.1.1, hypothesis 6.170.9, streamlit 1.66.1,
langchain-anthropic 1.7.6. No `.env` and no API key: W-2s were read by the offline reader
(`FixtureW2Extractor`), and all tax_math calls went through the real MCP server.

Before the baseline, `pydantic_core` and `cryptography` in `.venv` had lost their compiled files
and pytest could not start. They were reinstalled (`pip install --force-reinstall --no-deps
pydantic_core==2.50.1 cryptography==48.0.1`), and `streamlit` and `langchain-anthropic` (listed in
requirements.txt but missing) were installed.

## 3. Commands

```bash
source .venv/bin/activate
python -m pytest -q                       # whole suite
python -m pytest -q tests/app             # new: registration, eval cases, dashboard
```

## 4-6. Results

| | Baseline (`5b36b53`) | After integration |
|---|---|---|
| Passed | 752 | **796** |
| Failed | 2 | **0** |
| Skipped | 0 | 0 |

| Area | Tests | Result |
|---|---|---|
| `tests/tax_math` | 332 | passed |
| `tests/extraction` | 27 | passed |
| `tests/eligibility` | 85 | passed |
| `tests/computation` | 16 | passed |
| `tests/review` | 62 | passed |
| `tests/orchestrator` | 29 | passed (2 were failing at baseline) |
| `tests/test_intake.py`, `test_intake_integration.py`, `test_memory.py` | 185 | passed |
| `tests/test_decision_table.py`, `test_merge_order.py` | 30 | passed |
| `tests/app` (new) | 30 | passed |

## 7. Unresolved failures

None in the automated suite. Lint: no linter is configured in the repo; a user-global ruff
config reports style-only findings across the codebase (including the new files), not addressed.

## 8. New and repaired regression tests

| Test | Guards against | Fails without the fix? |
|---|---|---|
| `tests/orchestrator/test_graph.py::test_loop_guard_breaks_the_intake_eligibility_cycle`, `..._counts_again_after_a_person_steps_in` (repaired) | loop guard catches a real hand-off cycle (spec 4.1) | were failing on `main` (outdated scenario) |
| `test_intake_message_reaches_the_main_state`, `test_intake_requests_reach_the_main_state`, `test_a_field_requested_again_and_again_reaches_a_preparer_in_the_full_graph` | Intake outputs dropped at the subgraph boundary | yes, 3 failed with `state.py` at HEAD |
| `test_detect_escalations[...]` ×5, `test_ordinary_answers_are_not_escalated[...Roth IRA]` | spec's tax-advice examples not detected | yes, 4 failed |
| `test_not_sure_offers_a_person` | honest uncertainty path (spec 4.3) | wording test |
| `test_progress_counts_the_questions_other_agents_asked_for`, `test_a_summary_of_a_half_finished_interview_doesnt_claim_answers` | "13 of 13", "Dependents: none" when unanswered | yes, 2 failed |
| `test_a_real_looking_ssn_never_reaches_the_saved_session`, `test_the_standalone_interview_refuses_it_before_the_graph_too` | rejected real SSN persisted in SQLite | yes, 2 failed |
| `tests/app/test_documents.py` (18) | upload/typed-entry validation, IDs, duplicates, Eligibility reads typed forms | new feature |
| `tests/app/test_eval_cases.py` (9) | both eval cases, stop path, resume in a new process, repeated answer, terminal program | new feature |
| `tests/app/test_dashboard.py` (3) | dashboard taxpayer flow, resume, preparer resolving case 2 | new feature |

## 9. Evaluation case 1: clean run (automated and manual)

- Automated: `test_eval_case_1_clean_run`, `test_the_terminal_program_runs_eval_case_1_and_writes_the_form`,
  `test_a_taxpayer_runs_eval_case_1_in_the_dashboard`: passed.
- Manual, terminal (`python run.py session ... --offline`, answers typed from the file): route
  `intake → extraction → eligibility_rules → intake → extraction → eligibility_rules →
  computation → review → finalize`, `approved`, draft HTML written. Every line equals the
  hand-worked values in `demo/eval_case_1.md` (0 mismatches).
- **Not verified:** reading a W-2 image with the vision model (no API key available). The live
  path is the existing Extraction code; only the offline reader was exercised.

## 10. Evaluation case 2: human in the loop

- Automated: `test_eval_case_2_hitl_is_resolved_by_a_preparer` (pause, refused empty answer
  records nothing, decision, resume, `approved`), `test_eval_case_2_stop_hands_the_return_to_a_preparer`
  (`handed_to_preparer`, no draft), `test_a_preparer_resolves_eval_case_2_in_the_dashboard`: passed.
- Manual, terminal: `run.py session` paused with "Filing status unclear"; `printf 'Preparer
  Pat\n1\n2\n<note>\n' | python run.py review --session eval2 --db ...` recorded the decision
  and finished `approved` (43 s, Review's stdio MCP caller); `run.py session` again wrote the form.

## 11. Other human-in-the-loop checks (manual, through `app.session`)

| Input | Result |
|---|---|
| "my son forces me to give him my refund" | paused: "Possible financial exploitation or third-party pressure", options Checked safe (note required) / Stop |
| "should I convert my IRA to a Roth?" | **before the fix: no flag, run approved.** After: "Tax advice requested", options Noted / Stop |
| 1099-NEC in the document list | "An out-of-scope document was listed", Re-ask / Stop |
| 1099-R with box 2b checked | "1099-R taxable amount not determined", Stop only |
| Inventory says 1 SSA-1099, none supplied | "An answer doesn't match a document" |
| W-2 SSN differs from taxpayer's | "An answer doesn't match a document" |

## 12. Memory and session resumption

- Automated: `test_a_session_resumes_in_a_new_process_with_a_summary` (new graph on the same
  SQLite file continues at the same question; welcome message has progress and a masked summary),
  `test_coming_back_to_a_session_shows_what_was_covered` (dashboard), existing
  `test_a_saved_session_resumes_after_a_restart` and `tests/test_memory.py`: passed.
- Manual: terminal session stopped after 5 answers, re-run with the same `--session`, continued
  at question 6, then completed.
- Duplicate submissions: a repeated `question_id` is ignored (`test_a_repeated_answer_is_not_applied_to_the_next_question`);
  starting an existing session again changes nothing; the same W-2 file twice is refused.

## 13. Known limitations

- Vision-model W-2 reading not tested here (no API key).
- `docs/metrics.md` (spec 4.5 context-window measurements, `--no-context-strategy` batch) not done.
- `docs/graph.png` and the demo video not produced (Mermaid diagram is in the README).
- Dashboard has no authentication.
- `python run.py review` takes ~45 s per decision (Review's stdio MCP caller); the dashboard
  uses the in-process MCP client and takes ~1-2 s.
- Documents must be registered before the interview starts; a resumed session can't add more.
- Preparer notes are not screened for SSNs; direct `graph.invoke(Command(resume=...))` callers
  bypass the SSN pre-screen.
- Any SQLite session file created before the SSN fix may contain rejected SSNs in its `writes`
  table; delete local `data/sessions.sqlite`.
