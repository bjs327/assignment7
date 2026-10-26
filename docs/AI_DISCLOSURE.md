# AI-use disclosure

The assignment asks for one detailed paragraph on how the team used AI. Without it, the grade is
capped at 60/70.

## Final paragraph

TODO: combine each member's notes below into the final paragraph.

## Notes by component

### Computation, tax_math and inter-agent communication (Brendan)

These notes were drafted by the AI from the working session and need Brendan's review before
they go into the final paragraph.

- **Tool:** Claude Code in the Claude desktop app, with the Claude Opus 5.5 model, used
  throughout as a pair programmer. Brendan set the direction and approved each step; Claude
  proposed plans, wrote the code and tests, and ran them.
- **Planning:**
  - Prepared questions for the client Q&A, then turned the answers into design decisions.
  - Drafted the Computation row of the team's agent inputs and outputs sheet.
  - Reviewed the process map and suggested changes.
  - Drafted the plan for the Computation agent and the tax_math MCP server, which Brendan
    reviewed before any code was written.
- **tax_math and its MCP server:**
  - Claude wrote the `tax_math` package, the MCP server and about 330 tests.
  - Before coding, it checked the 2025 tax brackets and the IRS Pub. 915 Social Security
    worksheet against web sources. The deduction amounts come from the specification.
  - The expected values in the tests were worked out from the IRS worksheet and the published
    rate schedules, not by running the code.
  - Testing caught two problems, which Claude fixed: an error message that read differently
    through MCP than in a direct call, and a test that compared a rounded line with an unrounded
    one.
  - At Brendan's request, Claude then reviewed the code for mistakes and unnecessary complexity.
    It found 3 mistakes and 6 simplifications, and fixed all 9 once Brendan approved them.
- **Repository and team workflow:**
  - Claude arranged the files into the assignment's repository layout, with placeholder files
    for components other team members own, and made the first commit and push.
  - It reviewed and merged three teammates' pull requests (#2, #3 and #4). It test-merged each
    one, ran the full test suite, checked role boundaries and SSN handling, and resolved one
    conflict in `run.py` by keeping every command.
  - It drafted the repository overview and check-in status notes Brendan shared with the team.
- **Computation agent:**
  - Claude read the other agents' code to find the formats Computation receives (Extraction,
    Intake, Eligibility) and must produce (the `draft_return` that Review checks).
  - It wrote the agent (`src/agents/computation/`), three new rules in `data/policy.md`
    (LINE-01 to LINE-03) and 16 tests, including one that runs a sample return through the
    real Eligibility, Computation and Review agents. The expected line values were worked out by
    hand from the IRS rules.
  - Testing caught an error in Claude's own test data: a charitable IRA distribution needs the
    owner to be at least 70½. The Eligibility agent correctly escalated it, and the test data was
    fixed.
- **Inter-agent communication:** while doing the above, Claude identified the integration gaps
  recorded in `docs/IMPLEMENTATION_NOTES.md`:
  - The W-2 field names differ between Extraction's output and the Eligibility sample data.
  - Nobody collects the 1099 and SSA-1099 amounts or the Schedule 1 to 3 entries yet.
  - Eligibility and Review disagree on whether capital gain distributions may appear on line 7.
- **Brendan's decisions so far:**
  - Kept tax year 2025 after Claude pointed out that the specification requires it.
  - Asked for the code review to put simplicity first, and approved the fixes.
  - Chose where the repository lives, and directed the merges and the Computation work.
- **Brendan's own review:** TODO. Describe how you checked Claude's work and anything you changed
  yourself.

### Eligibility & Rules (David)

These notes were drafted by the AI and need David's review before they go into the final
paragraph.

- **Tool:** Claude Code in the VS Code extension, with the Claude Opus 5.5 model.
- **Research:** No reference corpus was supplied, so Claude read the 2025 Form 1040 instructions
  and the 2025 Instructions for Forms 1099-R and 5498 on irs.gov and turned the relevant rules
  (1040-SR eligibility, filing statuses, line 12d boxes, chart exclusions, senior deduction
  eligibility, 1099-R codes including the new code Y for charitable distributions) into the
  eligibility entries in `data/policy.md`, each citing its source.
- **Design:** Claude proposed making the decisions a deterministic, cited decision table rather
  than LLM output, and asking Intake for missing facts instead of assuming them. David agreed.
- **Building:** Claude wrote `src/agents/eligibility/` (corpus loader, facts adapter, rules
  engine, LangGraph node, CLI), two sample state files and about 85 tests. The expected values
  in the tests come from the IRS rules, not from running the code.
- **What testing caught:** a QCD test case used a taxpayer too young to be 70½ (the engine was
  right, the test data was wrong), and after a human switched a return to married filing
  jointly the agent didn't ask for the spouse's details. Claude fixed the second.
- **David's own role:** TODO. Describe your review, edits and decisions.


### Intake Agent and Agent Memory (Jordan)

- **Tool:** Claude Code in VS Code was used as a pair-programming assistant to develop, debug, and test the Intake Agent and Agent Memory. ChatGPT was also used for planning, troubleshooting LangGraph, and understanding integration requirements.

- **Planning:** Defined the Intake Agent's inputs, outputs, and responsibilities. Built an initial LangGraph prototype before integrating it into the team's GitHub repository.

- **Intake Agent:** Claude developed `src/agents/intake.py` to collect taxpayer information, handle corrections, validate test SSNs, and flag situations requiring human review. The agent asks questions individually and does not perform tax calculations or interpret tax rules.

- **Agent Memory:** Claude implemented SQLite checkpointing in `src/memory.py`, allowing taxpayers to pause and resume sessions while preserving their answers and conversation progress.

- **Testing and Debugging:** Claude generated automated tests and identified issues involving missing Eligibility questions, repeated requests, and LangGraph interrupts. These were addressed through additional question handling, improved state management, and duplicate-request protection.

- **Integration:** Claude helped identify shared-state issues between Intake, Eligibility, Computation, and the Orchestrator. Additional changes were made to improve communication and prevent infinite loops.

- **Jordan's Contributions:** I reviewed the assignment requirements, guided the implementation, configured LangGraph, tested the agent independently, verified session persistence, managed GitHub contributions, and coordinated integration improvements with teammates.
