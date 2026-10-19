"""Unit tests for the small pure functions."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from india_stock_mcp.server import num, pct_change, to_crore, to_yahoo_symbol


@pytest.mark.parametrize(
    ("symbol", "exchange", "expected"),
    [
        ("RELIANCE", "NSE", "RELIANCE.NS"),
        ("  reliance ", "NSE", "RELIANCE.NS"),
        ("TCS.NS", "NSE", "TCS.NS"),
        ("TCS.NS", "BSE", "TCS.BO"),
        ("M&M", "NSE", "M&M.NS"),
        ("BAJAJ-AUTO", "BSE", "BAJAJ-AUTO.BO"),
    ],
)
def test_to_yahoo_symbol(symbol: str, exchange: str, expected: str) -> None:
    assert to_yahoo_symbol(symbol, exchange) == expected


@pytest.mark.parametrize("bad", ["", "bad symbol!", "DROP TABLE;", "A" * 21, "../etc"])
def test_to_yahoo_symbol_rejects_invalid(bad: str) -> None:
    with pytest.raises(ToolError, match="Invalid symbol"):
        to_yahoo_symbol(bad)


def test_num_handles_missing_values() -> None:
    assert num(3.14159) == 3.14
    assert num(None) is None
    assert num(float("nan")) is None
    assert num(float("inf")) is None
    assert num("not a number") is None


def test_pct_change() -> None:
    assert pct_change(110, 100) == 10.0
    assert pct_change(90, 100) == -10.0
    assert pct_change(100, 0) is None  # no division by zero
    assert pct_change(None, 100) is None


def test_to_crore() -> None:
    assert to_crore(10_000_000) == 1.0
    assert to_crore(None) is None
