"""The rules tax_math relies on, by ID.

Every CalcResult cites IDs from this registry. The same IDs should appear in
data/policy.md so Eligibility & Rules, Computation and Review all cite one source.
"""

from dataclasses import dataclass

_SPEC = "SilverFile specification, section 3.2"
_CHART = "2025 Form 1040-SR, Standard Deduction Chart and line 12d"
_SCHEDULE_1A = "2025 Schedule 1-A (Form 1040), Part V"
_PUB_915 = "IRS Pub. 915 (2025), Worksheet 1; 2025 Form 1040 instructions, lines 6a and 6b"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    source: str


RULES: dict[str, Rule] = {
    rule.id: rule
    for rule in (
        Rule(
            "RND-01",
            "Whole-dollar rounding: keep cents while figuring, round only the result; "
            "50 cents or more rounds up",
            "2025 Form 1040 instructions, 'Rounding Off to Whole Dollars'",
        ),
        Rule(
            "SD-01",
            "Standard deduction base: $15,750 single or married filing separately, "
            "$31,500 married filing jointly or qualifying surviving spouse, $23,625 head of household",
            f"{_SPEC}; {_CHART}",
        ),
        Rule(
            "SD-02",
            "Each box checked on line 12d adds $2,000 if unmarried and not a surviving spouse, "
            "otherwise $1,600",
            f"{_SPEC}; {_CHART}",
        ),
        Rule(
            "SD-03",
            "Line 12d boxes: taxpayer born before January 2, 1961; taxpayer blind; spouse born before "
            "January 2, 1961; spouse blind. Spouse boxes apply only when married filing jointly or separately",
            _CHART,
        ),
        Rule(
            "S1A-01",
            "Enhanced deduction for seniors: $6,000 per qualifying person aged 65 or older, "
            "in addition to the age boxes on line 12d",
            f"{_SPEC}; {_SCHEDULE_1A}",
        ),
        Rule(
            "S1A-02",
            "Senior deduction phase-out: reduced by 6% of modified AGI over $75,000 "
            "($150,000 on a joint return), separately for each qualifying person",
            f"{_SPEC}; {_SCHEDULE_1A}",
        ),
        Rule(
            "SSA-01",
            "Provisional income: other income plus tax-exempt interest plus half of benefits, "
            "less adjustments to income",
            f"{_SPEC}; {_PUB_915}",
        ),
        Rule(
            "SSA-02",
            "Base amount $25,000 ($32,000 married filing jointly; $0 if married filing separately "
            "and lived with spouse) and second tier $34,000 ($44,000 married filing jointly)",
            f"{_SPEC}; {_PUB_915}",
        ),
        Rule(
            "SSA-03",
            "Taxable benefits are capped at 50% of benefits up to the second tier and at 85% of "
            "benefits overall",
            _PUB_915,
        ),
        Rule(
            "TAX-01",
            "2025 Tax Rate Schedules X (single), Y-1 (married filing jointly, qualifying surviving "
            "spouse), Y-2 (married filing separately) and Z (head of household)",
            "Rev. Proc. 2024-40; 2025 Form 1040 instructions, Tax Rate Schedules",
        ),
    )
}
