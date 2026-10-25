# SilverFile

A multi-agent preparation aid for Form 1040-SR, the U.S. Income Tax Return for Seniors (tax year
2025), built for Kushim. SilverFile gathers a client's information, drafts the return, and
records where every number came from so a person at Kushim can review it.

SilverFile never files anything and never gives tax advice. Anything out of scope, any conflict
between documents and answers, and any request for advice goes to a human.

## Status

| Component | Owner | In this repo |
|---|---|---|
| Orchestrator | Verena | Placeholder |
| Intake | Jordan | Placeholder |
| Document Extraction | Yassine | W-2 extraction agent done (LangGraph nodes, tests) |
| Eligibility & Rules | David | Placeholder |
| Computation | Brendan | `tax_math` MCP server done, agent node next |
| Review / Audit | Marco | Review agent done (independent re-check of every computed line, 3-round revision loop, 62 tests); LangGraph wiring next |

## Team roles

| Focus area | Team member assigned |
|---|---|
| Agent memory | TODO |
| Inter agent communication | Brendan |
| Task division and management | TODO |
| MLOps/DevOps | TODO |
| Guardrails and ethics | Marco |
| Security | Yassine |

## Setup

Requires Python 3.11 or newer (tested with 3.13).

```bash
python -m venv .venv
```

Activate it: `.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on macOS and
Linux. Then:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in. (The `tax_math` server doesn't need it yet.)

## Running

```bash
python run.py --help
```

```bash
python run.py tax-math-server
```

That starts the `tax_math` MCP server on stdin/stdout. Normally the Computation agent starts it
rather than a person. Commands for the rest of the system will be added to `run.py` as the
agents land.

To try the Document Extraction agent on the fake W-2s:

```bash
python data/generate_fixtures.py                                  # fake W-2 images
python run.py extract demo/w2_image_1.jpg demo/w2_image_2.jpg     # needs an API key in .env
python run.py extract demo/w2_image_1.jpg --offline               # no key needed
```

## Tests

```bash
python -m pytest
```

There are about 330 tests for `tax_math`, about 30 for Document Extraction and SSN security
(no API key needed), plus two skipped placeholders for tests other
components still need to write.

## Repository layout

```
├── README.md
├── requirements.txt
├── .env.example
├── run.py                       command-line entry point
├── pytest.ini
├── src/                         import root: code imports tax_math, state, agents.intake, ...
│   ├── state.py                 placeholder: shared state and reducers
│   ├── graph.py                 placeholder: LangGraph wiring
│   ├── memory.py                placeholder: checkpointer and context-window strategy
│   ├── agents/                  the six agents (extraction done, others placeholders)
│   ├── security/                done: SSN validation, masking and redaction
│   └── tax_math/                done: deterministic 2025 arithmetic and its MCP server
├── data/
│   ├── generate_fixtures.py     fake W-2 images with ground-truth JSON
│   ├── policy.md                reference corpus: computation rules done, eligibility rules TODO
│   └── fixtures/
├── evals/cases.json             placeholder
├── tests/
│   ├── test_merge_order.py      placeholder
│   ├── test_decision_table.py   placeholder
│   ├── extraction/              tests for Document Extraction and SSN security
│   └── tax_math/                tests for tax_math
├── traces/
├── docs/
│   ├── IMPLEMENTATION_NOTES.md  one section per component
│   ├── AI_DISCLOSURE.md
│   └── metrics.md               placeholder
└── demo/
    ├── eval_case_1.md           placeholder
    └── eval_case_2.md           placeholder
```

Still to add: `docs/graph.png` and the demo video in `demo/`.

## Orchestration

TODO: the orchestration diagram and the reasoning behind the topology (specification section 4.1).

## More detail

- [docs/IMPLEMENTATION_NOTES.md](docs/IMPLEMENTATION_NOTES.md): how each component works.
- [data/policy.md](data/policy.md): the rules the agents cite.
- [docs/AI_DISCLOSURE.md](docs/AI_DISCLOSURE.md): how the team used AI.
