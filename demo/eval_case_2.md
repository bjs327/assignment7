# Eval case 2: human-in-the-loop run

Inputs that trigger a human-in-the-loop escalation (specification section 4.4, condition 5:
filing-status ambiguity because a spouse died during the tax year), how to resolve it, and the
draft afterwards. Automated as `tests/app/test_eval_cases.py::test_eval_case_2_hitl_is_resolved_by_a_preparer`
and `tests/app/test_dashboard.py::test_a_preparer_resolves_eval_case_2_in_the_dashboard`.

## Inputs

The same W-2 image and typed-in forms as case 1 (`demo/w2_image_1.jpg`,
`demo/eval_case_1_forms.json`). The answers are in `demo/eval_case_2_answers.txt`: identical to
case 1 except question 15:

| # | Question | Answer |
|---|---|---|
| 15 | Did your husband or wife pass away during 2025? | **`yes`** |

## Step 1: trigger the escalation

```bash
python run.py session --session eval2 --w2 demo/w2_image_1.jpg --forms demo/eval_case_1_forms.json \
    --offline --answers demo/eval_case_2_answers.txt
```

Expected: the interview finishes, then the run pauses (a LangGraph `interrupt`) with:

```
This session is waiting for a Kushim preparer (1 issue(s)):
  - Filing status unclear: The taxpayer chose married filing jointly, and their spouse died during
    2025. Married filing jointly, single or another status may apply: a person needs to decide.
```

The taxpayer is told a preparer will be in touch; they are not shown the issue or advised. The
session is saved in `data/sessions.sqlite` and survives closing the terminal.

## Step 2: resolve it as a preparer

**Dashboard (recommended):** `python run.py dashboard --offline`, open the printed URL, choose
**Kushim preparer** in the sidebar. Session `eval2` is selected (state `preparer`, 1 open issue).
On the **Review** tab:

1. Type your name.
2. For "Filing status unclear" (rules FS-02, FS-06, ESC-05), choose **Choose the filing status yourself**.
3. Filing status: **Married filing jointly** (a surviving spouse may file jointly for the year of death).
4. Optionally add a note, then **Record decisions and continue**.

If the name or a decision is missing, nothing is recorded and the problem is shown.

**Terminal alternative:** `python run.py review --session eval2`, then type your name, `1`
(Choose the filing status yourself), `2` (Married filing jointly) and an optional note. It then
finishes the run, which takes about 45 seconds from the terminal (Review starts a separate
tax_math MCP server for each re-check). Run the `session` command from step 1 again (with
`--session eval2 --offline`) to write the draft to `out/eval2_1040sr.html`.

## Expected result

The run resumes from the checkpoint: Eligibility & Rules re-runs with the preparer's decision,
then Computation and Review, and the session ends `approved`. The draft (dashboard **Draft Form
1040-SR** tab, or run the `session` command again to write `out/eval2_1040sr.html`) has the same
amounts as case 1 (filing status married filing jointly, 12e $34,700), and its "Review and human
decisions" section records who chose what and when.

To see the other ending, choose **Stop** instead: the run ends `handed_to_preparer` and no draft
is produced (`test_eval_case_2_stop_hands_the_return_to_a_preparer`).

## Other HITL triggers you can try

Type these during any interview (dashboard or terminal):

| Say | Condition (spec 4.4) | What happens |
|---|---|---|
| `my son forces me to give him my refund` | 6, financial exploitation | Interview stops at once; preparer options: Checked: safe to continue (note required) or Stop |
| `I'd like to talk to a person` | voluntary request | Interview stops; a preparer is asked to contact the taxpayer |
| `should I convert my IRA to a Roth?` | 4, tax advice | Not answered; noted for the preparer; the interview continues |
| Listing a document you didn't supply (e.g. an extra SSA-1099) | 2, statement vs document conflict | "An answer doesn't match a document" |
| A 1099-R with box 2b checked (typed-in forms) | 3 | Escalated, Stop only |
| `1099-NEC` in the document list | 1, out of scope | Escalated |
