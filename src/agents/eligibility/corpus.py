"""The reference corpus, data/policy.md, as rule records the agent can cite.

Eligibility & Rules must be grounded in the corpus. Every decision it makes cites rule IDs,
and each citation is resolved here to the rule's title, text and source, so the audit record
quotes the rule rather than paraphrasing it. A citation that doesn't resolve is a bug, not a
judgement call: ``cite`` raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[3] / "data" / "policy.md"

# Same heading shape the tax_math provenance test checks: "### SD-01: Title".
_HEADING = re.compile(r"^### ([A-Z0-9]+-\d+): (.+)$", re.MULTILINE)
_SOURCE = re.compile(r"^Source: (.+)$", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class PolicyRule:
    id: str
    title: str
    text: str  # the rule body, without the Source line
    source: str


class UnknownRuleError(KeyError):
    """A rule ID was cited that has no entry in data/policy.md."""


def parse_policy(markdown: str) -> dict[str, PolicyRule]:
    rules: dict[str, PolicyRule] = {}
    headings = list(_HEADING.finditer(markdown))
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        body = markdown[match.end() : end]
        # Stop at the next "## " section heading if one falls inside the body.
        body = re.split(r"^## ", body, maxsplit=1, flags=re.MULTILINE)[0].strip()
        source_match = _SOURCE.search(body)
        source = " ".join(source_match.group(1).split()) if source_match else ""
        text = body[: source_match.start()].strip() if source_match else body
        rules[match.group(1)] = PolicyRule(match.group(1), match.group(2).strip(), text, source)
    return rules


@lru_cache(maxsize=1)
def load_policy(path: Path = POLICY_PATH) -> dict[str, PolicyRule]:
    return parse_policy(path.read_text(encoding="utf-8"))


def cite(rule_id: str) -> PolicyRule:
    try:
        return load_policy()[rule_id]
    except KeyError:
        raise UnknownRuleError(f"{rule_id} has no entry in {POLICY_PATH.name}") from None


def citation_records(rule_ids: list[str]) -> list[dict[str, str]]:
    """JSON-safe citations for the audit record: id, title and source, in the order given."""
    out = []
    for rule_id in dict.fromkeys(rule_ids):
        rule = cite(rule_id)
        out.append({"id": rule.id, "title": rule.title, "source": rule.source})
    return out
