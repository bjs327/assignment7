## Review / Audit (Marco)

Tries to prove the assembled draft return wrong before any human sees it. It never fixes anything: it returns a verdict and, for each problem, which agent should redo the work. Code: `src/agents/review/`. Tests: `tests/review/`.

### What it checks

Twelve independent, read-only checks (`checks.py`). A check that crashes becomes a blocking finding, so a broken check can never look like a pass.

| Check | Tries to falsify | Sent to |
|---|---|---|
| R01 draft status | Return is a draft; nothing marked filed | human |
| R02 provenance | Every line has a usable record; tool working ends at the printed number; typed-in values say who and when; extracted values name a document | whoever made the line |
| R03 replay | Re-runs each tool-made line through the same `tax_math` tool with the recorded inputs | Computation |
| R04 arithmetic | Recomputes lines 9, 11a, 14, 15, 18-24, 25d, 33, 34, 37 with its own addition, independent of the tools | whoever made the line |
| R05 contradictions | 3a > 3b, 4b > 4a, 5b > 5a, 6b > 6a, 6b > 85% of 6a (one dollar of rounding allowed) | whoever made the line |
| R06 stale inputs | Each tool was asked about the final value of the line it depends on (the 6b -> 9 -> 11a -> 13b ordering dependency) | Computation |
| R07 eligibility | Born before 2 Jan 1961 (either spouse if joint); 12d age boxes match dates of birth; 13b <= $6,000 per qualifying person | Eligibility / human |
| R08 scope | Out-of-scope documents, 1099-R "not determined", the three chart-does-not-apply cases, itemizing, capital gains | human |
| R09 source documents | Every document behind a line was actually extracted | Extraction / Intake |
| R10 SSN | No SSN outside 900-999 anywhere on the return; never repeated in the finding | human |
| R11 escalations and advice | No unresolved escalation; no advice-like text in notes | human |

### Verdicts and the loop

- `approved`: no blocking findings.
- `revise`: fixable blockers. `revision_requests` lists them per agent; `next_hops()` orders the agents (Intake, Extraction, Eligibility, Computation) so one pass of fixes is enough. Document ids for Extraction go in `reextract_document_ids`.
- `escalate`: any blocker that needs a human, or the loop is not converging. The Orchestrator raises the LangGraph interrupt.

**Bound.** At most `MAX_REVIEW_ITERATIONS = 3` reviews (two rounds of revision). A third review that still has blockers escalates with code `review_iteration_limit`.

**Loop guard.** Each review hashes its blocking findings (check, line, expected, actual). If a later review produces the same hash, the revision changed nothing, so the loop escalates immediately with `review_loop_detected` rather than waiting for the bound. `detect_handoff_cycle()` (`loopguard.py`) is a second, general guard for any A -> B -> A -> B pattern in the agent trace; the Orchestrator should call it before each routing decision. Tests: `test_iteration_bound_...`, `test_loop_guard_breaks_...`, `test_detect_handoff_cycle`.

### State keys

Reads `draft_return`, `documents`, `extracted_documents`, `escalations`, `review_iteration`, `review_fingerprints`. Writes `review_report`, `review_iteration`, `review_fingerprints` (append), `review_history` (append), `revision_requests`, `reextract_document_ids`, and `escalations` (append, same shape Extraction uses). `ReviewState` in `node.py` has the types and reducers to merge into `src/state.py`.

### What Review needs from the other agents

- **Computation:** each tool-made line carries `{value, source: "tool", tool, inputs, steps, citations}`, exactly the `tax_math` result plus the inputs it was called with. Typed-in lines: `{value, source: "user_input", entered_by, entered_at}`. Extracted lines: `{value, source: "extracted", documents: [ids]}`.
- **Computation:** record the line values each tool was called with under the `tax_math` input names. `INPUT_LINKS` in `checks.py` maps them (`net_benefits` -> 6a, `modified_agi` -> 11a, `taxable_income` -> 15). Check these names against `src/tax_math/server.py`.
- **Eligibility & Rules:** put `taxpayer.dob`, `spouse.dob`, `boxes_12d` and `scope_facts` on the draft return.
- **Orchestrator:** mark an escalation `resolved: true` when a human resolves it, and route using `next_hops(review_report)`.

### Failure modes and limits

- Review checks the return against itself and against `tax_math`. It cannot know a wrong date of birth or a wrong W-2 box; those are Intake's and Extraction's job (R09 only checks the document was usable).
- If `tax_math` and the Computation agent share a bug, replay agrees with the wrong answer. R04 and R07 use separate arithmetic for that reason, but only for sums, differences and age rules.
- The student-loan-interest nuance in the 6b worksheet is not independently checked.
- The advice check (R11) is a phrase list, so it is a tripwire, not a classifier.
- Replay starts one short-lived MCP server per tool-made line (about a dozen per review). Fine for a demo, slow at scale.
