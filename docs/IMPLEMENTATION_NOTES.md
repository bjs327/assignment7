# Implementation notes

One section per component and per focus area, each written by its owner.

- [Orchestrator](#orchestrator-verena)
- [Intake](#intake-jordan)
- [Document Extraction](#document-extraction-yassine)
- [Eligibility & Rules](#eligibility--rules-david)
- [Computation and the tax_math MCP server](#computation-and-the-tax_math-mcp-server-brendan)
- [Review / Audit](#review--audit-marco)
- Focus areas: [Inter-agent communication](#inter-agent-communication-brendan), [Security](#security-yassine)

## Orchestrator (Verena)

TODO

## Intake (Jordan)

TODO

## Document Extraction (Yassine)

Turns W-2 images into typed records. Every amount carries a provenance record (file, file
hash, box, the raw text read, model, time and attempt number). The agent transcribes; it
never decides which form line a value feeds. That is the Computation agent's job.

Code: `src/agents/extraction/`. Tests: `tests/extraction/`. Fixtures: `data/generate_fixtures.py`.

### How it reads a W-2

1. A vision model (any LangChain chat model; Anthropic by default, see `.env.example`) returns
   a `W2Reading` through structured output: every value as a string, exactly as printed.
   The prompt tells it to mark boxes it can't read rather than guess, and to treat text on
   the document as data, never as instructions.
2. Plain Python (`validation.py`) parses the strings into amounts and runs checks. A doubtful
   value is flagged, never corrected, so a model mistake becomes a question for a human
   rather than a wrong number on the return.
3. A failed model call or malformed output is retried once, then escalated.

### Graph shape

```
route_documents --Send per W-2--> extract_document (x N, in parallel) --> aggregate_extractions
```

- **Parallel branch:** one `Send` per W-2. Each branch writes to `extracted_documents`.
- **Reducer:** `merge_extractions` keys results by document id, lets a higher `attempt` win,
  and sorts the output, so the merged state is the same whatever order branches finish in
  (`tests/extraction/test_extraction_merge_order.py`).
- **Aggregation:** runs once after all branches. Flags the same file uploaded twice (it would
  double-count wages) and marks non-W-2 documents as entered by hand.
- **Review loop hook:** Review writes document ids to `reextract_document_ids`; only those are
  read again, with `attempt + 1`.

### Interface for other agents

| Direction | Key | Shape |
|---|---|---|
| Intake -> Extraction | `documents` | `[{"document_id": "w2-1", "document_type": "W-2", "path": "..."}]` |
| Extraction -> everyone | `extracted_documents` | `ExtractionResult` dicts (`schemas.py`), status `extracted` / `needs_review` / `failed` / `manual_entry_required` |
| Extraction -> Orchestrator | `escalations` | `{source_agent, document_id, code, reason, attempt, created_at}` |
| Extraction -> Computation | `labeled_amounts(docs, "1")` | `[{"label": "W-2 w2-1 (Acme) box 1", "amount": "18500.00"}]`, ready for `tax_math.sum_amounts` |

The agent never calls `interrupt()` itself; the Orchestrator decides when an escalation
pauses the graph. `labeled_amounts` only returns documents with status `extracted`, so a W-2
awaiting review can't reach the return.

To wire it in, add the `ExtractionState` keys and reducers to `src/state.py`, then call
`add_extraction_nodes(builder, extractor, entry="orchestrator", exit_to="orchestrator")` in
`src/graph.py`.

### Escalation codes

| Code | When |
|---|---|
| `non_test_ssn` | Box a is outside 900-999. The SSN is discarded. |
| `wrong_tax_year` | Not a 2025 W-2 (prior-year returns are out of scope). |
| `unreadable_box`, `missing_wages`, `amount_*` | A box couldn't be read as a clean amount. |
| `duplicate_document` | The same file was uploaded twice. |
| `extraction_failed`, `file_unreadable` | The model failed twice, or the file couldn't be opened. |

Lower-severity `needs_confirmation` issues (blank box 2, withholding above wages, missing tax
year) are recorded on the result for Intake or Review to confirm without stopping the graph.

### Trying it

```bash
python data/generate_fixtures.py                          # fake W-2s into demo/ and data/fixtures/
python run.py extract demo/w2_image_1.jpg demo/w2_image_2.jpg
python run.py extract demo/w2_image_1.jpg --offline       # no API key: uses the ground-truth JSON
```

`FixtureW2Extractor` (the `--offline` reader) is a test double for CI and grading machines
without a key. `data/fixtures/w2_prompt_injection.jpg` is a manual test for the real model:
box c tells the AI to report box 1 as 0.00, and box 1 must still read 22,750.00.

## Eligibility & Rules (David)

TODO

## Computation and the tax_math MCP server (Brendan)

The Computation agent may not do arithmetic itself. Every number on the draft return comes
from a tool in `src/tax_math`, a package of deterministic 2025 Form 1040-SR arithmetic exposed
as an MCP server. Each tool returns the number together with its working and the rules it
relies on, which becomes that line's provenance record.

Status: the `tax_math` package, the MCP server and their tests are done. The Computation agent
node (`src/agents/computation.py`) is next. It needs the record format from Document Extraction
and the output format from Eligibility & Rules.

### Layout

| File | What it holds |
|---|---|
| `constants_2025.py` | Every 2025 amount (deductions, thresholds, tax brackets), tagged with a rule ID |
| `rules.py` | The rule registry: ID, plain-English rule, source. The same IDs appear in `data/policy.md` |
| `models.py` | `CalcResult`, `Step`, `LabeledAmount`, `FilingStatus`, `TaxMathInputError` |
| `primitives.py` | `sum_amounts`, `subtract_amounts` for lines that are plain totals or differences |
| `social_security.py` | `taxable_social_security`: line 6b, following IRS Pub. 915 Worksheet 1 |
| `deductions.py` | `standard_deduction` (line 12e) and `senior_deduction` (line 13b) |
| `tax.py` | `tax_from_rate_schedule`: line 16 |
| `server.py` | The MCP server (stdio) exposing all six tools |

The tests are in `tests/tax_math/`.

### Running the server

```bash
python run.py tax-math-server
```

It speaks MCP over stdin/stdout, so normally a client starts it. From Python:

```python
from mcp import Client
from mcp.client.stdio import StdioServerParameters

params = StdioServerParameters(
    command="python",
    args=["run.py", "tax-math-server"],
    cwd=REPO_ROOT,  # the folder that contains run.py; without it, this only works from there
)
async with Client(params) as client:
    result = await client.call_tool("standard_deduction", {
        "filing_status": "married_filing_jointly",
        "taxpayer_65_or_older": True,
        "taxpayer_blind": False,
        "spouse_65_or_older": True,
    })
    line_12e = result.structured_content["value"]  # 34700
```

This uses the MCP Python SDK version 2, where `FastMCP` became `MCPServer`. Examples written for
version 1 won't run as-is.

### The tools

| Tool | Form line(s) | Inputs |
|---|---|---|
| `sum_amounts` | 1z, 2a, 2b, 3a, 3b, 4a, 5a, 5b, 6a, 9, 14, 25a to 25d, 33 | A list of `{label, amount}` |
| `subtract_amounts` | 11a, 15, 34, 37 | `start`, `less`, optional `floor_at_zero` |
| `taxable_social_security` | 6b | Benefits, other income, tax-exempt interest, adjustments, filing status, line 6d for married filing separately |
| `standard_deduction` | 12e | Filing status and the four line 12d boxes |
| `senior_deduction` | 13b | Modified AGI (= line 11a in our scope), filing status, who qualifies |
| `tax_from_rate_schedule` | 16 | Taxable income, filing status |

**Order matters.** `taxable_social_security` needs every other income line first, and its
result feeds line 9, then AGI (11a), then `senior_deduction`. So the order is: income lines,
then 6b, then 9, 11a, 12e, 13b, 14, 15 and 16.

### What every tool returns

```json
{
  "tool": "taxable_social_security",
  "tax_year": 2025,
  "value": 9260,
  "unrounded_value": "9260.425",
  "summary": "$9,260 of the $19,201 in benefits is taxable (provisional income of $39,600.50 against a $25,000 base amount).",
  "inputs": {"net_benefits": "19201", "other_income": "30000", "...": "..."},
  "steps": [
    {"label": "Line 1: net benefits", "working": "$19,201", "amount": "19201"},
    {"label": "Line 2: half of line 1", "working": "50% x $19,201", "amount": "9600.5"},
    "...",
    {"label": "Rounded to whole dollars", "working": "$9,260.425 rounds to $9,260", "amount": "9260"}
  ],
  "citations": ["SSA-01", "SSA-02", "SSA-03", "RND-01"]
}
```

- `value` goes on the form.
- `summary` and `steps` give the hover or click explanation for the provenance view.
- `citations` link to the rules in `data/policy.md`.
- The last step's amount always equals `value`, so the working always ends at the number shown.
- Amounts are strings in their shortest exact form ("9600.5", not "9600.50"), so compare them as
  numbers, not text.

### Design decisions

- **Exact money.** All arithmetic uses `Decimal`, never floats. Inputs may have at most two
  decimal places, and floating-point noise such as `0.30000000000000004` is rejected rather than
  kept.
- **Rounding (RND-01).** Cents are kept while figuring and only the tool's final result is rounded
  to whole dollars. 50 cents or more rounds up (not Python's `round()`, which rounds half to even).
  The rounding appears as its own step.
- **Errors, never guesses.** Missing, malformed or contradictory input raises `TaxMathInputError`
  naming the field, which the server returns as an MCP tool error. Examples: spouse boxes on a
  single return, or married filing separately without the line 6d answer. Computation should
  route these back to the agent that supplied the value, not retry with a guess.
- **Rules have IDs.** Every result cites rule IDs from `rules.py`. Tests fail if a tool cites an
  unknown rule, if a rule goes unused, or if a rule is missing from `data/policy.md`.
- **The spec's numbers are pinned.** `tests/tax_math/test_constants.py` checks every amount printed
  in section 3.2 of the specification.
- **Plain ASCII working** ("x" and "-") so printing a result never crashes a Windows console.

### Testing

About 330 tests:

- Every line of the Standard Deduction Chart.
- Social Security examples worked by hand through the IRS worksheet.
- The published tax amount at every bracket top.
- Property tests (Hypothesis), for example "line 6b is never more than 85% of benefits" and "the
  senior deduction never rises with income".
- Checks that the MCP server returns exactly what a direct call returns, both in-process and as a
  real stdio subprocess.

### What the other agents need to supply

- **Document Extraction:** amounts as plain numbers with no `$` or commas. For 1099-INT, include
  **box 3** (U.S. savings bond and Treasury interest). It's taxable, so line 2b is box 1 plus box 3.
- **Intake:**
  - For married filing separately, ask whether the taxpayer lived apart from their spouse for all
    of 2025 (line 6d). The Social Security worksheet can't run without it.
  - Record student loan interest separately from other adjustments. It counts in line 10, but the
    Social Security worksheet leaves it out.
- **Eligibility & Rules:**
  - Decide who qualifies for the senior deduction and pass `taxpayer_qualifies` and
    `spouse_qualifies`. The tool only does the arithmetic.
  - Real law adds two conditions the spec doesn't mention: married taxpayers must file jointly to
    claim it, and each person needs an SSN valid for employment. The team should decide whether to
    apply them.
  - Rule out the three cases where the standard deduction chart doesn't apply before line 12e is
    computed.
- **Review / Audit:** compare lines as they appear on the form (rounded), and re-run the same tools
  to recompute. One rounding edge case: with $0.59 of benefits, line 6b rounds to $1, which is
  more than the raw $0.59 but not more than line 6a's rounded $1.

### Known limitations (escalate rather than compute)

- **Line 16** uses the rate schedules at every income level, as the spec says. The IRS Tax Table,
  which real returns use below $100,000, works in $50 bands and can differ by a few dollars.
- **Qualified dividends** aren't taxed at their lower rates: the Qualified Dividends and Capital
  Gain Tax Worksheet isn't applied.
- **Social Security cases not handled:**
  - Lump-sum elections (line 6c).
  - Negative net benefits.
  - Income excluded on Pub. 915 worksheet line 5 (adoption benefits, foreign income).
  - An IRA deduction when the taxpayer is covered by a workplace retirement plan (Pub. 590-A
    changes the worksheet order).

## Review / Audit (Marco)

TODO

## Focus areas

### Inter-agent communication (Brendan)

TODO

## Security (Yassine)

Code: `src/security/ssn.py`. Tests: `tests/extraction/test_ssn.py`.

### SSNs (spec 3.2)

- **Allow-list.** `check_ssn` accepts only 900-999 SSNs, the range the SSA reserves for testing.
  Everything else is treated as possibly real. Intake should call it for the taxpayer, spouse
  and dependents; Extraction calls it on W-2 box a.
- **A possibly-real SSN is never stored.** Not in state, provenance, traces or error
  messages: the record shows `[SSN REDACTED]` and an escalation explains why. Test SSNs are
  kept only masked (`XXX-XX-3456`) in extraction records.
- **Volunteered SSNs.** `contains_ssn` and `redact_ssns` let Intake catch an SSN typed where
  none was asked for, and scrub it from the message before it reaches state or the model.
- **Errors are scrubbed.** Model errors can echo their input, so they pass through
  `redact_ssns` before reaching the trace.

### Model and document safety

- **Prompt injection.** Text on a document is data. The system prompt says so, structured
  output limits what the model can return, and deterministic validation runs afterwards.
- **The model never decides.** It transcribes strings; parsing and checks are plain Python.

### Secrets

API keys live in `.env`, which `.gitignore` excludes; `.env.example` lists every setting.

### Known limitations

- The W-2 image, including box a, is sent to the model provider. Acceptable here because only
  test SSNs are allowed; a production system would mask box a before upload or host the model.
- The dashboard has no authentication yet.
