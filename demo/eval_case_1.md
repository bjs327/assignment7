# Eval case 1: clean run

A complete set of inputs that produces a finished draft Form 1040-SR with no human-in-the-loop
event. Automated as `tests/app/test_eval_cases.py::test_eval_case_1_clean_run` and
`tests/app/test_dashboard.py::test_a_taxpayer_runs_eval_case_1_in_the_dashboard`.

## The taxpayers (fictional)

Margaret and her husband, married filing jointly, both born before 2 January 1961, neither blind,
no dependents. Test SSNs only (900-999 range).

## Inputs

| Input | File / value |
|---|---|
| W-2 image | `demo/w2_image_1.jpg` (Maple Grove Public Library: box 1 $18,500.00, box 2 $1,850.00) |
| Typed-in forms | `demo/eval_case_1_forms.json`: SSA-1099 box 5 $24,000 (taxpayer) and $16,000 (spouse); 1099-INT box 1 $1,200, box 8 $300; 1099-R box 1 and 2a $40,000, box 4 $4,000, code 7, IRA box checked |
| Interview answers | `demo/eval_case_1_answers.txt`, one per line, in order (below) |

| # | Question | Answer |
|---|---|---|
| 1 | Filing status | `married filing jointly` |
| 2 | Your SSN | `900-12-3456` |
| 3 | Your date of birth | `03/02/1958` |
| 4 | Are you legally blind? | `no` |
| 5 | Spouse's SSN | `901-22-3333` |
| 6 | Spouse's date of birth | `07/19/1960` |
| 7 | Is your spouse legally blind? | `no` |
| 8 | Any dependents? | `no` |
| 9 | Which tax documents? | `a W-2, 2 SSA-1099s, a 1099-INT and a 1099-R` |
| 10 | Any estimated payments? | `yes` |
| 11 | How many? | `2` |
| 12-13 | Each payment | `1000`, `1000` |
| 14 | Summary correct? | `yes` |
| 15 | (asked for Eligibility & Rules) Spouse passed away in 2025? | `no` |
| 16 | Can anyone claim you as a dependent? | `no` |
| 17 | U.S. citizen or resident for only part of 2025? | `no` |
| 18 | Can anyone claim your spouse as a dependent? | `no` |
| 19 | Summary correct? | `yes` |

Questions 15-18 are not on Intake's own list: Eligibility & Rules asks for them through the
Orchestrator, and Intake asks them one at a time.

## Run it

Offline (no API key: the W-2 is read by the offline reader from the ground truth stored next to
the image):

```bash
python run.py session --session eval1 --w2 demo/w2_image_1.jpg --forms demo/eval_case_1_forms.json \
    --offline --answers demo/eval_case_1_answers.txt
```

Leave out `--answers` to type the answers yourself. Leave out `--offline` (and put
`ANTHROPIC_API_KEY` in `.env`) to have the vision model read the W-2 image.

In the dashboard: `python run.py dashboard --offline`, open the printed URL, choose **Taxpayer**,
press **Add demo W-2 1**, type in the four forms from the table above with **Add this form**,
press **Start my questions**, and give the answers above.

## Expected result

`Run status: approved`, route `intake → extraction → eligibility_rules → intake → extraction →
eligibility_rules → computation → review → finalize`, no human review. The draft form is written
to `out/eval1_1040sr.html` (or shown in the dashboard), and every amount opens its provenance.

| Line | Amount | Line | Amount |
|---|---|---|---|
| 1z | 18,500 | 14 | 46,700 |
| 2a / 2b | 300 / 1,200 | 15 | 47,000 |
| 3a / 3b | 0 / 0 | 16 | 5,163 |
| 4a / 4b | 40,000 / 40,000 | 24 | 5,163 |
| 5a / 5b | 0 / 0 | 25a / 25b / 25c | 1,850 / 4,000 / 0 |
| 6a / 6b | 40,000 / 34,000 | 25d | 5,850 |
| 9 | 93,700 | 26 | 2,000 |
| 11a | 93,700 | 33 | 7,850 |
| 12e | 34,700 (31,500 + 2 × 1,600) | 34 (overpaid) | 2,687 |
| 13b | 12,000 (6,000 each, no phase-out) | 37 (owed) | 0 |

Worked by hand (tests/computation/scenarios.py): 6b: provisional income 59,700 + 300 + 20,000 =
80,000; the joint worksheet gives 6,000 + 85% × 36,000 = 36,600, capped at 85% × 40,000 = 34,000.
Tax on 47,000 (Schedule Y-1): 2,385 + 12% × 23,150 = 5,163.
