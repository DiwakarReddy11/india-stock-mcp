"""End-to-end tool tests through a real MCP client, connected in-process (no network)."""

import json

import pytest
from mcp import Client

from india_stock_mcp.server import mcp

EXPECTED_TOOLS = {
    "get_stock_quote",
    "get_price_history",
    "get_technical_analysis",
    "get_fundamentals",
    "compare_stocks",
    "get_market_overview",
    "get_market_status",
}


async def call(name: str, arguments: dict) -> tuple[bool, str]:
    async with Client(mcp) as client:
        result = await client.call_tool(name, arguments)
        return result.is_error, result.content[0].text


async def call_ok(name: str, arguments: dict) -> dict:
    is_error, text = await call(name, arguments)
    assert not is_error, text
    return json.loads(text)


async def test_all_tools_are_registered_as_read_only() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all(tool.annotations.read_only_hint for tool in tools)


async def test_get_stock_quote() -> None:
    quote = await call_ok("get_stock_quote", {"symbol": "reliance"})
    assert quote["symbol"] == "RELIANCE.NS"
    assert quote["price"] > 0
    assert quote["previous_close"] is not None
    assert quote["currency"] == "INR"


async def test_unknown_symbol_returns_clean_error() -> None:
    is_error, text = await call("get_stock_quote", {"symbol": "NOSUCHSTOCK"})
    assert is_error
    assert "No price data found for NOSUCHSTOCK.NS" in text


async def test_invalid_symbol_is_rejected() -> None:
    is_error, text = await call("get_stock_quote", {"symbol": "bad symbol!"})
    assert is_error
    assert "Invalid symbol" in text


async def test_invalid_period_is_rejected_by_schema() -> None:
    is_error, _ = await call("get_price_history", {"symbol": "TCS", "period": "100y"})
    assert is_error


async def test_get_price_history() -> None:
    history = await call_ok("get_price_history", {"symbol": "TCS", "period": "1y"})
    assert len(history["candles"]) == 250  # capped
    assert history["summary"]["return_percent"] > 0  # fake data trends up


async def test_technical_analysis_reads_uptrend_signals() -> None:
    analysis = await call_ok("get_technical_analysis", {"symbol": "INFY"})
    signals = " | ".join(analysis["signals"])
    # The fake data rises every day, so price is above both averages and RSI is pinned at 100.
    assert "Price above 50-day SMA" in signals
    assert "above 200-day SMA" in signals
    assert "overbought" in signals
    assert analysis["rsi_14"] == 100
    assert analysis["overall_trend"] in {"bullish", "neutral", "bearish"}
    assert "not investment advice" in analysis["disclaimer"]


async def test_get_fundamentals_converts_units() -> None:
    data = await call_ok("get_fundamentals", {"symbol": "RELIANCE"})
    assert data["market_cap_crore"] == 1_600_000.0  # 16 lakh crore
    assert data["roe_percent"] == 9.12


async def test_compare_stocks_reports_bad_symbols_per_row() -> None:
    result = await call_ok("compare_stocks", {"symbols": ["TCS", "INFY", "NOSUCHSTOCK"]})
    rows = {row["symbol"]: row for row in result["comparison"]}
    assert rows["TCS.NS"]["return_1y_percent"] > 0
    assert "error" in rows["NOSUCHSTOCK.NS"]


@pytest.mark.parametrize("symbols", [["TCS"], ["A", "B", "C", "D", "E", "F"]])
async def test_compare_stocks_validates_count(symbols: list[str]) -> None:
    is_error, text = await call("compare_stocks", {"symbols": symbols})
    assert is_error
    assert "between 2 and 5" in text


async def test_market_overview_handles_partial_failures() -> None:
    overview = await call_ok("get_market_overview", {})
    rows = {row["index"]: row for row in overview["indices"]}
    assert rows["NIFTY50"]["value"] > 0  # known to the fake
    assert "error" in rows["SENSEX"]  # unknown to the fake: reported, not crashed
    assert "is_open" in overview["market_status"]
