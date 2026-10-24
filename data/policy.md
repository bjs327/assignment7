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

TODO (Eligibility & Rules: David). Entries still needed, from the specification:

- 1040-SR eligibility (section 3.2).
- Filing status validation, including a spouse who died during the year (section 4.4).
- In-scope and out-of-scope items (section 3.3).
- Escalation conditions (section 4.4).
- No tax advice (section 2), and only test-range SSNs, 900 to 999 (section 3.2).
