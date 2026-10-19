"""Technical indicator maths."""

import pandas as pd

from india_stock_mcp.server import bollinger, macd, rsi


def test_rsi_is_100_when_price_only_rises() -> None:
    rising = pd.Series(range(1, 60), dtype=float)
    assert rsi(rising).iloc[-1] == 100


def test_rsi_is_0_when_price_only_falls() -> None:
    falling = pd.Series(range(60, 1, -1), dtype=float)
    assert rsi(falling).iloc[-1] == 0


def test_rsi_stays_between_0_and_100() -> None:
    zigzag = pd.Series([100, 103, 99, 104, 98, 105, 101, 99, 102, 100] * 6, dtype=float)
    values = rsi(zigzag).dropna()
    assert ((values >= 0) & (values <= 100)).all()


def test_macd_is_positive_in_an_uptrend() -> None:
    line, signal, histogram = macd(pd.Series(range(1, 100), dtype=float))
    assert line.iloc[-1] > 0
    assert histogram.iloc[-1] == line.iloc[-1] - signal.iloc[-1]


def test_bollinger_bands_are_ordered() -> None:
    close = pd.Series([100, 102, 101, 103, 99, 104, 100, 102] * 5, dtype=float)
    upper, middle, lower = (band.iloc[-1] for band in bollinger(close))
    assert lower < middle < upper
