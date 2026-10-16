"""Smoke test: connect to the MCP server like an AI client would, and call every tool.

Usage:
    uv run python scripts/smoke_test.py                      # local server
    uv run python scripts/smoke_test.py https://<your-url>/mcp   # deployed server
"""

import asyncio
import json
import sys

from mcp import Client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"

CALLS = [
    ("get_market_status", {}),
    ("get_stock_quote", {"symbol": "RELIANCE"}),
    ("get_technical_analysis", {"symbol": "INFY"}),
    ("get_fundamentals", {"symbol": "HDFCBANK"}),
    ("compare_stocks", {"symbols": ["TCS", "INFY", "WIPRO"]}),
    ("get_market_overview", {}),
    ("get_stock_quote", {"symbol": "bad symbol!"}),  # should return a clean error
]


async def main() -> None:
    async with Client(URL) as client:
        tools = await client.list_tools()
        print(f"Connected to {URL}")
        print("Tools:", [tool.name for tool in tools.tools], "\n")

        for name, args in CALLS:
            result = await client.call_tool(name, args)
            text = result.content[0].text
            status = "ERROR" if result.is_error else "OK"
            print(f"--- {name}({args}) -> {status}")
            print(text if result.is_error else json.dumps(json.loads(text), indent=2)[:600], "\n")


asyncio.run(main())
