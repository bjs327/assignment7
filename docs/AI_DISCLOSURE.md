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
