# Team review summary: end-to-end integration

Branch `integration/end-to-end` (local, from `main` at `5b36b53`). **Nothing is committed, pushed
or merged.** Full detail: `docs/INTEGRATION_CHANGELOG.md`; test evidence:
`docs/INTEGRATION_TEST_REPORT.md`; baseline audit: `docs/INTEGRATION_AUDIT.md`.

Result: 796 tests pass (baseline 752 passed, 2 failed). Both eval cases run end to end from the
terminal and the dashboard with the offline W-2 reader. No change is approved by anyone but
Jordan yet.

## Review table

| Component | Changes made | Files affected | Test status | Owner review | Merge readiness |
|---|---|---|---|---|---|
| Orchestrator (Verena) | Shared state: declared `intake_requests`, `intake_message`; `intake_status` may be `"awaiting_human"`. Loop-guard tests: new cycle scenario (faulty Eligibility stub via monkeypatch), assertions unchanged | `src/state.py`, `tests/orchestrator/test_graph.py` | passing | **REQUIRES OWNER REVIEW** | Pending Verena |
| Orchestrator (Verena) | `run.py`: new `session` and `dashboard` commands (existing commands unchanged) | `run.py` | passing | **REQUIRES OWNER REVIEW** (shared entry point) | Pending |
| Intake and Agent Memory (Jordan) | Tax-advice detection for the spec's examples; progress count; summary wording; "talk to a person" on uncertainty; real SSNs refused before the graph (`ssn_refusal`); notes section written | `src/agents/intake.py`, `tests/test_intake.py`, `tests/test_intake_integration.py`, `docs/IMPLEMENTATION_NOTES.md` | passing | Jordan (owner) | Ready after Jordan's review |
| Document Extraction (Yassine) | None to Extraction's code. New upload interface writes `documents` in Extraction's existing format | none of Yassine's files | passing | Informational: please confirm the `documents` entry format (`document_id`, `document_type`, `path`, plus `sha256`, `registered_at`) | Pending |
| Eligibility & Rules (David) | None to David's code. Typed-in forms now reach `entered_documents` with extra keys `entered_by`, `entered_at`, `source` (ignored by `DocumentFacts`) | none of David's files | passing | Informational: please confirm | Pending |
| Computation and MCP (Brendan) | None to Brendan's code. The app passes `mcp_tool_caller()` (in-process MCP) to Review | none of Brendan's files | passing | Informational | Pending |
| Review / Audit (Marco) | None to Marco's code. In the app, Review gets the in-process MCP caller instead of its default stdio caller (one server per call, ~38 s per run) | `src/app/session.py` | passing | **REQUIRES OWNER REVIEW**: is the in-process MCP client acceptable for Review's independent re-check? | Pending Marco |
| Shared state and dashboard (owner to confirm) | New `src/app/`: document registration, session runner, draft Form 1040-SR with click-through provenance, Streamlit dashboard; `streamlit` added to requirements; `.gitignore` for `data/uploads/`, `out/` | `src/app/*`, `tests/app/*`, `requirements.txt`, `.gitignore` | passing (30 tests) | **Needs an owner** (README listed none) | Pending team decision |
| Docs (everyone) | README rewritten in places (statuses, run commands, diagram); eval cases, `evals/cases.json` | `README.md`, `demo/*`, `evals/cases.json` | n/a | **REQUIRES OWNER REVIEW** for each owner's status row and the Orchestration paragraph | Pending |

## Completed

- State fix for Intake's outputs; loop-guard tests repaired.
- Document registration (W-2 upload, typed-in forms and lines with "direct user input" provenance).
- Full session from the terminal (`python run.py session`) and the dashboard (`python run.py dashboard`).
- Draft Form 1040-SR (HTML) with provenance one click away; download from the dashboard.
- Eval cases 1 and 2 with input files, instructions and automated tests.
- Intake defects: advice detection, SSN persisted in SQLite, progress count, summary wording.

## Proposed (not done)

- **Verena:** the Orchestrator section of `docs/IMPLEMENTATION_NOTES.md` still says the loop
  guard "catches a real cycle today: Eligibility asks for facts Intake doesn't ask yet". That is
  no longer true; suggest describing the faulty-agent scenario now in the tests.
- **Verena:** after Intake re-runs for extra questions, Extraction is marked stale and every W-2
  is read again (a second paid model call per image). Consider not marking Extraction stale when
  only `ADDITIONAL_QUESTIONS` changed.
- **Marco:** `run.py review` (Verena's) builds the graph with Review's stdio caller and the
  vision extractor; it could share `app.session.open_graph` for speed and an `--offline` option.

## Partially done / blocked

- **Vision-model W-2 reading:** verified on `demo/w2_image_1.jpg` (eval case 1 approved, all lines
  correct). Still to try: a made-up W-2 the system hasn't seen (rubric D).
- **`docs/metrics.md` (spec 4.5):** the 10-item batch with `--no-context-strategy` and per-item
  token counts is not implemented. Agent memory has no assigned owner in the README.
- **`docs/graph.png`, demo video:** not produced.

## Unresolved defects and risks

- Dashboard has no login; anyone with the URL can act as a preparer.
- Documents can only be added before the interview starts.
- Preparer notes aren't screened for SSNs.
- Local SQLite files from before the SSN fix may contain rejected SSNs: delete `data/sessions.sqlite`.

## Decisions needing team agreement

1. Who owns `src/app/` (upload interface and dashboard), and the README "Agent memory",
   "Task division" and "MLOps/DevOps" roles.
2. Whether Review may use the in-process MCP client in the app.
3. Whether to accept the `state.py` additions as-is.
4. Who runs the live vision-model check and records the demo video.

## Approval checklist before merging

- [ ] Verena: `src/state.py`, `tests/orchestrator/test_graph.py`, `run.py`, README Orchestration paragraph
- [ ] Marco: in-process MCP caller for Review in `src/app/session.py`
- [ ] Yassine: `documents` entry format from `src/app/documents.py`
- [ ] David: extra keys on `entered_documents`
- [ ] Brendan: typed-in form boxes match Computation's `BOX_LINES` / `REQUIRED_BOXES`
- [ ] Team: owner for `src/app/`; README status rows
- [ ] Jordan: Intake changes; then commit, push and open a PR (not done; awaiting approval)
- [x] Eval case 1 with the vision model (demo W-2)
- [ ] A made-up W-2 with the vision model
