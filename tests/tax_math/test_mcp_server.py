"""The MCP server must return exactly what tax_math returns when called directly."""

import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tax_math import CalcResult, TaxMathInputError
from tax_math.server import build_server

from .helpers import ERROR_SAMPLES, FUNCTIONS, SAMPLES, ids

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.anyio


async def test_lists_every_tool_with_schemas_and_descriptions():
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    assert sorted(tool.name for tool in tools) == sorted(FUNCTIONS)
    for tool in tools:
        assert tool.description
        assert tool.output_schema
        assert tool.annotations.read_only_hint and tool.annotations.idempotent_hint
        for name, schema in tool.input_schema["properties"].items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


@pytest.mark.parametrize("name, arguments", SAMPLES, ids=ids(SAMPLES))
async def test_matches_a_direct_call(name, arguments):
    async with Client(build_server()) as client:
        result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert CalcResult.model_validate(result.structured_content) == FUNCTIONS[name](**arguments)


@pytest.mark.parametrize("name, arguments, field", ERROR_SAMPLES, ids=ids(ERROR_SAMPLES))
async def test_bad_input_comes_back_as_a_tool_error(name, arguments, field):
    with pytest.raises(TaxMathInputError) as raised:
        FUNCTIONS[name](**arguments)
    assert raised.value.field == field
    async with Client(build_server()) as client:
        result = await client.call_tool(name, arguments)
    assert result.is_error
    assert str(raised.value) in result.content[0].text


async def test_runs_as_a_stdio_subprocess_via_run_py():
    name, arguments = SAMPLES[4]  # the Social Security worksheet, with rounding
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "run.py"), "tax-math-server"],
        cwd=str(ROOT),
    )
    async with Client(params, read_timeout_seconds=30) as client:
        result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert CalcResult.model_validate(result.structured_content) == FUNCTIONS[name](**arguments)
