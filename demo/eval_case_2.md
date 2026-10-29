# Eval case 2: human-in-the-loop run

Inputs that trigger a human-in-the-loop escalation (specification section 4.4, condition 5:
filing-status ambiguity because a spouse died during the tax year), how to resolve it, and the
draft afterwards. Automated as `tests/app/test_eval_cases.py::test_eval_case_2_hitl_is_resolved_by_a_preparer`
and `tests/app/test_dashboard.py::test_a_preparer_resolves_eval_case_2_in_the_dashboard`.

## Inputs

The same W-2 image and typed-in forms as case 1 (`demo/w2_image_1.jpg`,
`demo/eval_case_1_forms.json`). The answers are in `demo/eval_case_2_answers.txt`: identical to
case 1 except question 15, plus two more at the end:

| # | Question | Answer |
|---|---|---|
| 15 | Did your husband or wife pass away during 2025? | **`yes`** |
| 20 | On what date did your husband or wife pass away? | **`03/10/2025`** |
| 21 | Summary correct? | `yes` |

Question 20 is asked only because the spouse (born 07/19/1960) turned 65 during 2025: whether
they count as 65 depends on whether they had reached 65 when they died (policy ELIG-02). A spouse
born earlier was already 65 when the year began, so SilverFile wouldn't ask. It is asked before the
escalation, so the taxpayer answers everything in one sitting and the preparer sees the date.

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
finishes the run, which takes one to two minutes from the terminal with no output while it works
(Review starts a separate tax_math MCP server for each re-check). Run the `session` command from
step 1 again (with `--session eval2 --offline`) to write the draft to `out/eval2_1040sr.html`.

## Expected result

The run resumes from the checkpoint: Eligibility & Rules re-runs with the preparer's decision,
then Computation and Review, and the session ends `approved`. The draft (dashboard **Draft Form
1040-SR** tab, or run the `session` command again to write `out/eval2_1040sr.html`) is married
filing jointly, and its "Review and human decisions" section records who chose what and when.

The income lines are the same as case 1. The deductions differ, because the spouse died on
March 10, 2025, before reaching 65 on July 18, 2025 (the day before the 65th birthday). Only the
taxpayer's age box on line 12d is checked, and only the taxpayer gets the senior deduction
(ELIG-02, ELIG-03; Form 1040 instructions, line 12d, and Publication 501, "Death of spouse"):

| Line | Amount | How |
|---|---|---|
| 12d | 1 box | You 65 or older; spouse not 65 at death |
| 12e | 33,100 | 31,500 + 1 × 1,600 |
| 13b | 6,000 | Taxpayer only; MAGI 93,700 is under 150,000 |
| 14 | 39,100 | 33,100 + 6,000 |
| 15 | 54,600 | 93,700 − 39,100 |
| 16 / 24 | 6,075 | Schedule Y-1: 2,385 + 12% × (54,600 − 23,850) |
| 33 | 7,850 | 5,850 withheld + 2,000 estimated |
| 34 (overpaid) | 1,775 | 7,850 − 6,075 |
| 37 (owed) | 0 | |

Every other line matches case 1 (1z 18,500, 6a 40,000, 6b 34,000, 9 and 11a 93,700, 25d 5,850).

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
