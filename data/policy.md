# SilverFile policy corpus (tax year 2025)

The reference material SilverFile's agents ground their decisions in. Every rule has a stable
ID. Agents cite these IDs, and the provenance record for each line of the draft return lists
the rules it relied on.

Sources are the SilverFile specification (the client's brief) and the IRS documents named in
each rule. Where the two differ, the specification governs.

## Computation rules

Cited by the `tax_math` tools (`src/tax_math/rules.py`). A test checks that every rule ID there
appears in this file, so rename IDs in both places together.

### RND-01: Rounding to whole dollars

Amounts go on the return in whole dollars. Keep cents while figuring a line and round only the
result: drop amounts under 50 cents and round 50 to 99 cents up to the next dollar. Round every
line the same way.

Source: 2025 Form 1040 instructions, "Rounding Off to Whole Dollars".

### SD-01: Standard deduction base amounts

| Filing status | Base amount |
|---|---|
| Single; married filing separately | $15,750 |
| Married filing jointly; qualifying surviving spouse | $31,500 |
| Head of household | $23,625 |

The Standard Deduction Chart does not apply in three cases, and all three escalate to a human:

- The taxpayer can be claimed as someone else's dependent.
- The taxpayer is married filing separately and the spouse itemizes.
- The taxpayer was a dual-status alien.

Source: SilverFile specification, section 3.2; 2025 Form 1040-SR, Standard Deduction Chart.

### SD-02: Additional amount per box checked

Each box checked on line 12d adds $2,000 if the taxpayer is unmarried and not a surviving spouse
(single or head of household). Otherwise each box adds $1,600 (married filing jointly or
separately, or qualifying surviving spouse).

Source: SilverFile specification, section 3.2; 2025 Form 1040-SR, Standard Deduction Chart.

### SD-03: The line 12d boxes

There are four boxes:

- You were born before January 2, 1961.
- You are blind.
- Your spouse was born before January 2, 1961.
- Your spouse is blind.

The spouse boxes apply only to married filing jointly and married filing separately. On a
separate return, they may be checked only in the limited circumstances the Form 1040-SR
instructions describe, which Eligibility & Rules decides.

Source: 2025 Form 1040-SR, line 12d.

### S1A-01: Enhanced deduction for seniors

An extra deduction of $6,000 for each qualifying person aged 65 or older, that is, born before
January 2, 1961. It reaches $12,000 when both spouses qualify on a joint return.

- It is claimed on Schedule 1-A and carried to Form 1040-SR line 13b.
- It comes on top of the extra standard deduction for age (the line 12d boxes), not instead of it.
- It applies for tax years 2025 to 2028.

Source: SilverFile specification, section 3.2; 2025 Schedule 1-A (Form 1040), Part V.

### S1A-02: Senior deduction phase-out

The deduction is reduced by 6% of modified adjusted gross income over $75,000 ($150,000 on a
joint return). The reduction applies separately to each qualifying person, and the deduction
never goes below zero. It is fully phased out at $175,000 ($250,000 joint).

In SilverFile's scope, modified AGI equals AGI (line 11a), because the foreign income exclusions
that would be added back are out of scope.

Example: a joint return where both spouses qualify, with modified AGI of $160,000. The reduction
is 6% × $10,000 = $600 each, leaving $5,400 each and $10,800 in total.

Source: SilverFile specification, section 3.2; 2025 Schedule 1-A (Form 1040), Part V.

### SSA-01: Provisional income

Only part of Social Security benefits may be taxable. Provisional income is:

- other income (Form 1040 lines 1z, 2b, 3b, 4b, 5b, 7a and 8),
- less adjustments to income (Schedule 1 lines 11 to 20, 23 and 25, which is every adjustment
  except the student loan interest deduction),
- plus tax-exempt interest (line 2a),
- plus half of net benefits (line 6a).

Line 6b feeds total income (line 9) and AGI, so it must be figured after every other income line
and before line 9.

Source: SilverFile specification, section 3.2; IRS Pub. 915 (2025), Worksheet 1.

### SSA-02: Base and second-tier amounts

| Filing status | Base amount | Second tier |
|---|---|---|
| Married filing jointly | $32,000 | $44,000 |
| Single, head of household, qualifying surviving spouse, or married filing separately and lived apart from the spouse all year | $25,000 | $34,000 |
| Married filing separately and lived with the spouse at any time in 2025 | $0 | None: 85% of provisional income counts, capped as in SSA-03 |

None of the benefits are taxable if provisional income is not over the base amount.

Source: SilverFile specification, section 3.2; IRS Pub. 915 (2025), Worksheet 1.

### SSA-03: Limits on the taxable part

- **Up to the second tier:** the taxable amount is the smaller of half the benefits and half of
  provisional income over the base amount.
- **Above the second tier:** add 85% of provisional income over the second tier. The first part
  then uses half of the $9,000 ($12,000 joint) gap between the base and the second tier.
- **Overall cap:** the taxable amount never exceeds 85% of net benefits.

Source: IRS Pub. 915 (2025), Worksheet 1.

### TAX-01: 2025 Tax Rate Schedules

Line 16 is figured from the 2025 Tax Rate Schedules at every income level. Each rate applies to
taxable income up to the amount shown.

| Rate | Schedule X (single) | Schedule Y-1 (married filing jointly, qualifying surviving spouse) | Schedule Y-2 (married filing separately) | Schedule Z (head of household) |
|---|---|---|---|---|
| 10% | $11,925 | $23,850 | $11,925 | $17,000 |
| 12% | $48,475 | $96,950 | $48,475 | $64,850 |
| 22% | $103,350 | $206,700 | $103,350 | $103,350 |
| 24% | $197,300 | $394,600 | $197,300 | $197,300 |
| 32% | $250,525 | $501,050 | $250,525 | $250,500 |
| 35% | $626,350 | $751,600 | $375,800 | $626,350 |
| 37% | above that | above that | above that | above that |

Real returns with taxable income under $100,000 use the IRS Tax Table, which can differ by a few
dollars. Qualified dividends are not given their lower rates in this version.

Source: Rev. Proc. 2024-40; 2025 Form 1040 instructions, Tax Rate Schedules; SilverFile
specification, section 3.1 (line 16).

## Eligibility, scope and escalation rules

Cited by the Eligibility & Rules agent (`src/agents/eligibility/`). Its decisions quote these
entries, and a test checks that every rule ID the agent can cite has a heading here.

### ELIG-01: Who can use Form 1040-SR

Form 1040-SR is available to a taxpayer born before January 2, 1961. On a joint return only
one spouse needs to meet this. On any other return, including married filing separately,
only the taxpayer's own date of birth counts. There is no income limit.

A taxpayer who doesn't qualify files Form 1040 instead. That is outside SilverFile's
service, so it escalates to a human (ESC-01).

Source: SilverFile specification, section 3.2; 2025 Form 1040 instructions, "Form 1040-SR"
(https://www.irs.gov/instructions/i1040gi).

### ELIG-02: Checking the line 12d boxes

- **Age:** check the box for a person born before January 2, 1961. A person is considered to
  reach age 65 on the day before their 65th birthday, so someone born on January 1, 1961 is 65
  at the end of 2025.
- **Blindness:** check the box for a person who was blind at the end of 2025.
- **Spouse boxes:** checked on a joint return. Never on single, head of household or
  qualifying surviving spouse. On married filing separately, only if the spouse had no
  income, isn't filing a return, and can't be claimed as a dependent on someone else's return.
- **A spouse who died in 2025:** the age box is checked only if the spouse reached 65 before
  death. SilverFile escalates every death in the tax year anyway (FS-06).

Source: 2025 Form 1040 instructions, line 12d, "Born before January 2, 1961, or blind"
(https://www.irs.gov/instructions/i1040gi).

### ELIG-03: Who qualifies for the senior deduction

A person qualifies for the Schedule 1-A enhanced deduction for seniors (S1A-01) if:

- they were born before January 2, 1961;
- they have a valid SSN (in SilverFile, a test SSN in the 900 to 999 range; GRD-03);
- if married, they file jointly. Married filing separately gets no senior deduction.

On a joint return each spouse is checked separately. The spouse amount applies only to a
joint return.

Source: SilverFile specification, section 3.2; 2025 Form 1040 instructions, line 13b and
"Married Filing Separately" (https://www.irs.gov/instructions/i1040gi); 2025 Schedule 1-A
(Form 1040), Part V.

### ELIG-04: When the Standard Deduction Chart doesn't apply

The chart on Form 1040-SR does not apply, and SilverFile escalates (ESC-01), if:

- someone can claim the taxpayer (or the spouse on a joint return) as a dependent, because
  the Standard Deduction Worksheet for Dependents applies instead;
- the taxpayer is married filing separately and the spouse itemizes, because the standard
  deduction is then zero;
- the taxpayer was a dual-status alien, because the standard deduction is then zero.

Source: SilverFile specification, section 3.2; 2025 Form 1040 instructions, lines 12a to 12d
(https://www.irs.gov/instructions/i1040gi).

### FS-01: Single

Unmarried on December 31, 2025: never married, legally separated under a decree of divorce
or separate maintenance, or widowed before January 1, 2025 and not remarried in 2025.

Source: 2025 Form 1040 instructions, "Filing Status", "Single"
(https://www.irs.gov/instructions/i1040gi).

### FS-02: Married filing jointly

Married at the end of 2025, even if not living together. A taxpayer whose spouse died in
2025, and who didn't remarry in 2025, can also file jointly. SilverFile needs details for
both spouses.

Source: 2025 Form 1040 instructions, "Married Filing Jointly"
(https://www.irs.gov/instructions/i1040gi).

### FS-03: Married filing separately

Married at the end of 2025 and filing separately. Each spouse generally reports only their
own income. Separate filers can't take the enhanced senior deduction (ELIG-03). Whether the
spouses lived apart all year changes the Social Security base amount (SSA-02, line 6d), so
SilverFile needs the answer.

Source: 2025 Form 1040 instructions, "Married Filing Separately" and line 6d
(https://www.irs.gov/instructions/i1040gi).

### FS-04: Head of household

The taxpayer must be unmarried, or considered unmarried, and either:

- paid over half the cost of keeping up a home that was the main home all year of a parent
  they can claim as a dependent; or
- paid over half the cost of keeping up a home they lived in, where a qualifying person lived
  more than half the year.

SilverFile needs the taxpayer to confirm they paid over half the cost of the home, and to
list at least one dependent as the qualifying person. Without one, the status is ambiguous
(ESC-05). Whether a person meets every qualifying-person test is not decided in SilverFile.

Source: 2025 Form 1040 instructions, "Head of Household"
(https://www.irs.gov/instructions/i1040gi).

### FS-05: Qualifying surviving spouse

All of these must be true:

- the spouse died in 2023 or 2024, and the taxpayer didn't remarry before the end of 2025
  (a spouse who died in 2025 rules this status out for 2025);
- the taxpayer can claim a child or stepchild (not a foster child);
- that child lived in the taxpayer's home all of 2025;
- the taxpayer paid over half the cost of keeping up the home.

A test that fails, or isn't answered, is filing-status ambiguity (ESC-05).

Source: 2025 Form 1040 instructions, "Qualifying Surviving Spouse"
(https://www.irs.gov/instructions/i1040gi).

### FS-06: A spouse who died during the tax year

If the taxpayer's spouse died in 2025, the right status could be married filing jointly,
single or (in later years) qualifying surviving spouse, and the line 12d age box depends on
the date of death (ELIG-02). SilverFile never picks one. It escalates to a human
(ESC-05), whatever status the taxpayer chose, and continues only with the status the human
confirms.

Source: SilverFile specification, section 4.4; 2025 Form 1040 instructions, "Married Filing
Jointly" and "Qualifying Surviving Spouse" (https://www.irs.gov/instructions/i1040gi).

### SCOPE-01: The in-scope return

SilverFile prepares only this profile:

- income from Forms W-2, SSA-1099, 1099-R, 1099-INT and 1099-DIV;
- any of the five filing statuses;
- the standard deduction (lines 12d and 12e) and the Schedule 1-A senior deduction;
- Schedules 1, 2 and 3 as amounts the taxpayer enters directly.

Anything else escalates (ESC-01).

Source: SilverFile specification, section 3.3.

### SCOPE-02: Out-of-scope items

These always escalate to a human, never attempted:

| Item | Typical document |
|---|---|
| Itemized deductions (Schedule A) | |
| Self-employment or business income (Schedule C) | 1099-NEC, 1099-K, 1099-MISC |
| Rental income (Schedule E) | Schedule K-1, 1099-MISC |
| Capital gains requiring Schedule D | 1099-B, 1099-S; 1099-DIV with boxes 2b to 2d |
| Digital assets | 1099-DA, 1099-B |
| Foreign income or accounts | foreign tax paid on 1099-INT box 6 or 1099-DIV box 7 |
| Amended or prior-year returns | a document for another tax year |
| State returns | |

A document type SilverFile doesn't recognise is treated as out of scope until a human says
otherwise.

Source: SilverFile specification, section 3.3.

### SCOPE-03: 1099-R with "taxable amount not determined"

When box 2b of a Form 1099-R has "Taxable amount not determined" checked, the payer couldn't
work out the taxable part. Working it out needs the Simplified Method or the General Rule,
which are out of scope and not in `tax_math`. These forms always escalate (ESC-03),
whatever else is known.

Source: SilverFile specification, section 3.3; 2025 Instructions for Forms 1099-R and 5498,
box 2b (https://www.irs.gov/pub/irs-prior/i1099r--2025.pdf).

### SCOPE-04: 1099-R distribution codes in scope

Only these box 7 codes are in scope:

- **7**: normal distribution (the participant is at least 59½).
- **7 with Y**: a qualified charitable distribution (QCD) from a non-inherited IRA. Code Y is
  new for 2025 and is always paired with 4, 7 or K. The QCD must meet the age rule in SCOPE-05.

Every other code escalates (ESC-01). Examples: 1 or 2 (early distribution, Form 5329),
4 (death benefit to a beneficiary), G or H (direct rollover), Q (Roth IRA), K (assets without a
market value).

The IRA/SEP/SIMPLE box sends the distribution to lines 4a and 4b. Otherwise it goes to lines 5a
and 5b (pensions and annuities).

Source: SilverFile specification, sections 3.1 and 3.3; 2025 Instructions for Forms 1099-R and
5498, Table 1 "Guide to Distribution Codes" and the IRA/SEP/SIMPLE checkbox
(https://www.irs.gov/pub/irs-prior/i1099r--2025.pdf); 2025 Form 1040 instructions, lines 4a to 5b.

### SCOPE-05: Age rule for a qualified charitable distribution

The IRA owner must have been at least 70½ when the QCD was made. A person who isn't 70½ by
December 31, 2025 can't have made a 2025 QCD. That contradicts the document, so it escalates
(ESC-02).

Source: 2025 Form 1040 instructions, lines 4a and 4b, "Exception 3"
(https://www.irs.gov/instructions/i1040gi).

### SCH-01: Which schedules apply

| Schedule | Applies when |
|---|---|
| Schedule 1 | The taxpayer enters adjustments (line 10) or other income amounts. Entered directly. |
| Schedule 1-A | At least one person qualifies for the senior deduction (ELIG-03). |
| Schedule 2 | The taxpayer enters additional taxes. Entered directly. |
| Schedule 3 | The taxpayer enters credits or other payments. Entered directly. |
| Schedule B | Taxable interest (line 2b) or ordinary dividends (line 3b) over $1,500. Decided once Computation has those lines. |
| Schedules A, C, D, E | Never. They are out of scope (SCOPE-02). |

Source: SilverFile specification, section 3.3; 2025 Form 1040 instructions, lines 2b and 3b
(https://www.irs.gov/instructions/i1040gi).

### ESC-01: Escalate anything out of scope

Any item in SCOPE-02, any 1099-R code outside SCOPE-04, a taxpayer who can't use Form 1040-SR
(ELIG-01) and any case where the Standard Deduction Chart doesn't apply (ELIG-04).

Source: SilverFile specification, sections 2, 3.3 and 4.4.

### ESC-02: Escalate a conflict between the taxpayer and a document

For example: a W-2 whose employee SSN matches neither the taxpayer nor a spouse on a joint
return; more or fewer documents than the taxpayer listed; a QCD for someone under 70½.
SilverFile doesn't choose which one is right. A human does.

Source: SilverFile specification, section 4.4.

### ESC-03: Escalate a 1099-R with box 2b checked

See SCOPE-03.

Source: SilverFile specification, sections 3.3 and 4.4.

### ESC-04: Escalate a request for tax advice

Questions about planning or optimisation ("Should I convert to a Roth?", "Would we pay less
filing separately?") are advice. SilverFile explains its scope and notes the question for a
volunteer preparer. Intake detects these; Eligibility & Rules never answers them either.

Source: SilverFile specification, sections 2 and 4.4.

### ESC-05: Escalate filing-status ambiguity

Any filing status that the taxpayer's answers don't clearly support (FS-01 to FS-05), and
always a spouse who died during 2025 (FS-06).

Source: SilverFile specification, section 4.4.

### ESC-06: Escalate signs of financial exploitation

For example, a refund routed to an account the taxpayer can't describe, or a third party
answering for the taxpayer with no stated authority. SilverFile notices and stops; it doesn't
judge. Intake detects these.

Source: SilverFile specification, section 4.4.

### GRD-01: Preparation aid, not a preparer or adviser

SilverFile never files anything and never gives tax advice. Its output is a draft return and
an audit record for a human. Transcription and arithmetic are in scope; optimisation and
planning are not.

Source: SilverFile specification, section 2.

### GRD-02: Never assert an unsourced amount or an unanswered fact

Every amount on the draft has a provenance record. Eligibility & Rules never assumes an answer
the taxpayer hasn't given: a missing fact goes back to Intake as a question, never a default.

Source: SilverFile specification, sections 2 and 4.2.

### GRD-03: Test SSNs only

SilverFile accepts only SSNs whose first three digits are 900 to 999, the range the
government reserves for testing. Anything else might be a real SSN. It is never stored or
repeated, and the session escalates.

Source: SilverFile specification, section 3.2.
