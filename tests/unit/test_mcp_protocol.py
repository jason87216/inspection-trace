import asyncio

from mcp import Client

from inspection_trace.mcp_server import server


def test_mcp_declares_bounded_read_tools_and_investigation_context() -> None:
    async def check() -> None:
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "trace_lot",
                "get_lot_history",
                "check_qtime",
                "query_records",
                "spc_status",
                "compare_groups",
                "get_import_errors",
            }
            assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools)
            assert len((await client.list_resources()).resources) == 3
            assert {prompt.name for prompt in (await client.list_prompts()).prompts} == {
                "investigate_anomaly"
            }
            rejected = await client.call_tool(
                "trace_lot", {"lot_no": "L261102A001", "max_depth": 11}
            )
            assert rejected.is_error

    asyncio.run(check())
