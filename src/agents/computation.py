"""Computation: fills in every derived line of the draft return.

It never does arithmetic itself: every number comes from a tax_math MCP tool call (start the
server with `python run.py tax-math-server`), and each line is stored with the tool's result
as its provenance record.

Next step (Brendan): build this node. The tools and their order are described in
docs/IMPLEMENTATION_NOTES.md.
"""
