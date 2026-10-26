# AI-use disclosure

The assignment asks for one detailed paragraph on how the team used AI. Without it, the grade is
capped at 60/70.

## Final paragraph

TODO: combine each member's notes below into the final paragraph.

## Notes by component

### Computation and tax_math (Brendan)

These notes were drafted by the AI and need Brendan's review before they go into the final
paragraph.

- **Tool:** Claude Code in the Claude desktop app, with the Claude Opus 5.5 model.
- **Planning:**
  - Prepared questions for the client Q&A, then turned the answers into design decisions.
  - Drafted the Computation row of the team's agent inputs and outputs sheet.
  - Reviewed the process map and suggested changes.
  - Drafted the plan for the Computation agent and the tax_math MCP server.
- **Building (steps 1 to 3 of that plan):**
  - Claude wrote the `tax_math` package, the MCP server and about 330 tests.
  - Before coding, it checked the 2025 tax brackets and the IRS Pub. 915 Social Security
    worksheet against web sources. The deduction amounts come from the specification.
  - The expected values in the tests were worked out from the IRS worksheet and the published
    rate schedules, not by running the code.
  - Testing caught two problems, which Claude fixed: an error message that read differently
    through MCP than in a direct call, and a test that compared a rounded line with an unrounded
    one.
  - Claude also arranged the files into the assignment's repository layout and created
    placeholder files for components other team members own.
- **Brendan's own role:** TODO. Describe your review, edits and decisions. For example, you kept
  tax year 2025 after Claude pointed out that the specification requires it.

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
