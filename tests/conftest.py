"""Shared test fixtures. Tests never call Yahoo: yfinance is replaced with a fake."""

import math

import pandas as pd
import pytest

import india_stock_mcp.server as server

KNOWN_TICKERS = {"RELIANCE.NS", "TCS.NS", "INFY.NS", "^NSEI"}


def make_prices(days: int = 300, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Deterministic daily OHLCV candles: a steady uptrend with a small wave on top."""
    index = pd.bdate_range(end="2026-10-16", periods=days, tz="Asia/Kolkata")
    close = [start + step * i + 2 * math.sin(i / 3) for i in range(days)]
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in close],
            "High": [c + 1 for c in close],
            "Low": [c - 1 for c in close],
            "Close": close,
            "Volume": [1_000_000 + i for i in range(days)],
        },
        index=index,
    )


class FakeTicker:
    """Stands in for yfinance.Ticker. Unknown symbols return no data, like the real one."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker

    def history(self, period: str = "1y", interval: str = "1d", auto_adjust: bool = False) -> pd.DataFrame:
        return make_prices() if self.ticker in KNOWN_TICKERS else pd.DataFrame()

    @property
    def info(self) -> dict:
        if self.ticker not in KNOWN_TICKERS:
            return {}
        return {
            "longName": "Reliance Industries Limited",
            "sector": "Energy",
            "marketCap": 16_000_000_000_000,
            "trailingPE": 24.5,
            "returnOnEquity": 0.0912,
        }


@pytest.fixture(autouse=True)
def fake_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """Applied to every test: fake Yahoo, no retry delays, empty caches."""
    monkeypatch.setattr(server.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(server, "UPSTREAM_BACKOFF_SECONDS", 0)
    for cache in (server._quote_cache, server._history_cache, server._info_cache):
        cache.clear()
