"""India Stock MCP server: tools for analysing NSE/BSE listed stocks.

Data comes from Yahoo Finance (via yfinance) and may be delayed ~15 minutes.
Nothing returned by this server is investment advice.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Callable
from datetime import datetime, time
from time import sleep
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
import uvicorn
import yfinance as yf
from cachetools import TTLCache
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from india_stock_mcp.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("india_stock_mcp")
# yfinance logs every unknown symbol at ERROR level; we already return a clean message instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

Exchange = Literal["NSE", "BSE"]
Period = Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"]
Interval = Literal["1d", "1wk", "1mo"]

EXCHANGE_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}

# Friendly index names -> Yahoo Finance index tickers.
INDICES = {
    "NIFTY50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYIT": "^CNXIT",
    "NIFTYAUTO": "^CNXAUTO",
    "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTYFMCG": "^CNXFMCG",
    "INDIAVIX": "^INDIAVIX",
}

# Trading symbols: letters, digits, '&' and '-' (e.g. M&M, BAJAJ-AUTO). The same symbols work for BSE.
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9&\-]{1,20}$")
MAX_COMPARE = 5
ONE_CRORE = 10_000_000

# Yahoo intermittently returns empty data or errors under load, so upstream calls are retried.
UPSTREAM_ATTEMPTS = 3
UPSTREAM_BACKOFF_SECONDS = 0.5

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

# --------------------------------------------------------------------------- #
# Caching - many users asking for the same stock should cost one upstream call.
# --------------------------------------------------------------------------- #

_quote_cache: TTLCache = TTLCache(maxsize=2_000, ttl=settings.quote_cache_ttl)
_history_cache: TTLCache = TTLCache(maxsize=1_000, ttl=settings.history_cache_ttl)
_info_cache: TTLCache = TTLCache(maxsize=1_000, ttl=settings.info_cache_ttl)


async def _cached(cache: TTLCache, key: tuple, fetch: Callable[[], Any]) -> Any:
    """Return a cached value, or run the blocking `fetch` in a worker thread and cache it."""
    if key in cache:
        return cache[key]
    # yfinance is synchronous; running it in a thread keeps the server responsive.
    value = await asyncio.to_thread(fetch)
    cache[key] = value
    return value


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def to_yahoo_symbol(symbol: str, exchange: Exchange = "NSE") -> str:
    """Convert a user symbol like 'reliance' or 'TCS.NS' into a Yahoo ticker like 'RELIANCE.NS'."""
    cleaned = symbol.strip().upper()
    for suffix in EXCHANGE_SUFFIX.values():
        cleaned = cleaned.removesuffix(suffix)
    if not SYMBOL_PATTERN.fullmatch(cleaned):
        raise ToolError(
            f"Invalid symbol '{symbol}'. Use the NSE/BSE trading symbol, e.g. RELIANCE, TCS, HDFCBANK, M&M."
        )
    return f"{cleaned}{EXCHANGE_SUFFIX[exchange]}"


def num(value: Any, digits: int = 2) -> float | None:
    """Round a number for JSON output; NaN/None/invalid become None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


def pct_change(new: Any, old: Any) -> float | None:
    new, old = num(new, 6), num(old, 6)
    if new is None or not old:
        return None
    return round((new - old) / old * 100, 2)


def to_crore(value: Any) -> float | None:
    number = num(value, 0)
    return None if number is None else round(number / ONE_CRORE, 2)


def call_upstream(ticker: str, fetch: Callable[[], Any], is_empty: Callable[[Any], bool]) -> Any:
    """Call Yahoo with retries. Returns None if every attempt came back empty."""
    failure: Exception | None = None
    for attempt in range(1, UPSTREAM_ATTEMPTS + 1):
        try:
            result = fetch()
            if not is_empty(result):
                return result
            failure = None
        except Exception as exc:  # yfinance raises many types: network, rate limit, parsing
            failure = exc
            logger.warning("Upstream call for %s failed (attempt %d): %r", ticker, attempt, exc)
        if attempt < UPSTREAM_ATTEMPTS:
            sleep(UPSTREAM_BACKOFF_SECONDS * attempt)
    if failure is not None:
        raise ToolError(f"Market data provider is temporarily unavailable for {ticker}. Try again shortly.")
    return None


def fetch_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    df = call_upstream(
        ticker,
        lambda: yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False),
        lambda df: df is None or df.empty,
    )
    if df is None:
        raise ToolError(f"No price data found for {ticker}. Check the symbol and exchange.")
    return df.dropna(subset=["Close"])


async def get_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    return await _cached(
        _history_cache, (ticker, period, interval), lambda: fetch_history(ticker, period, interval)
    )


def fetch_quote(ticker: str) -> dict[str, Any]:
    df = fetch_history(ticker, period="1mo", interval="1d")
    last = df.iloc[-1]
    previous_close = df["Close"].iloc[-2] if len(df) > 1 else None
    quote = {
        "symbol": ticker,
        "price": num(last["Close"]),
        "previous_close": num(previous_close),
        "change": num(last["Close"] - previous_close) if previous_close is not None else None,
        "change_percent": pct_change(last["Close"], previous_close),
        "open": num(last["Open"]),
        "day_high": num(last["High"]),
        "day_low": num(last["Low"]),
        "volume": int(last["Volume"]) if num(last["Volume"]) is not None else None,
        "as_of": df.index[-1].date().isoformat(),
        "currency": "INR",
    }
    if previous_close is None:
        # Yahoo sometimes returns only today's candle (common for BSE tickers).
        quote["note"] = "Previous close unavailable from the data source; try exchange='NSE'."
    return quote


async def get_quote(ticker: str) -> dict[str, Any]:
    return await _cached(_quote_cache, (ticker,), lambda: fetch_quote(ticker))


def fetch_info(ticker: str) -> dict[str, Any]:
    info = call_upstream(
        ticker,
        lambda: yf.Ticker(ticker).info or {},
        lambda info: not info.get("longName") and not info.get("shortName"),
    )
    if info is None:
        raise ToolError(f"No company information found for {ticker}. Check the symbol and exchange.")
    return info


# --------------------------------------------------------------------------- #
# Technical indicators (pure pandas, no extra dependency)
# --------------------------------------------------------------------------- #


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (12, 26, 9): returns (macd line, signal line, histogram)."""
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal, line - signal


def bollinger(close: pd.Series, window: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: returns (upper, middle, lower)."""
    middle = close.rolling(window).mean()
    std = close.rolling(window).std()
    return middle + k * std, middle, middle - k * std


# --------------------------------------------------------------------------- #
# MCP server
# --------------------------------------------------------------------------- #

mcp = MCPServer(
    name="india-stock-mcp",
    version="0.1.0",
    instructions=(
        "Tools for analysing Indian stocks listed on NSE and BSE. "
        "Use NSE trading symbols such as RELIANCE, TCS, INFY, HDFCBANK (exchange defaults to NSE). "
        "Prices are in INR and may be delayed ~15 minutes. "
        "Market cap values are in crore (1 crore = 10 million). "
        "Always tell the user that this is not investment advice."
    ),
)


@mcp.tool(annotations=READ_ONLY)
async def get_stock_quote(symbol: str, exchange: Exchange = "NSE") -> dict[str, Any]:
    """Get the latest price of an Indian stock: price, change %, open, day high/low and volume.

    Args:
        symbol: NSE/BSE trading symbol, e.g. "RELIANCE", "TCS", "INFY", "HDFCBANK".
        exchange: "NSE" (default) or "BSE".
    """
    return await get_quote(to_yahoo_symbol(symbol, exchange))


@mcp.tool(annotations=READ_ONLY)
async def get_price_history(
    symbol: str,
    exchange: Exchange = "NSE",
    period: Period = "6mo",
    interval: Interval = "1d",
) -> dict[str, Any]:
    """Get historical OHLCV candles for an Indian stock plus a summary of the period's performance.

    Args:
        symbol: NSE/BSE trading symbol, e.g. "TATAMOTORS".
        exchange: "NSE" (default) or "BSE".
        period: How far back to look: 1mo, 3mo, 6mo, 1y, 2y or 5y.
        interval: Candle size: 1d (daily), 1wk (weekly) or 1mo (monthly).
    """
    ticker = to_yahoo_symbol(symbol, exchange)
    df = await get_history(ticker, period, interval)
    candles = [
        {
            "date": index.date().isoformat(),
            "open": num(row["Open"]),
            "high": num(row["High"]),
            "low": num(row["Low"]),
            "close": num(row["Close"]),
            "volume": int(row["Volume"]),
        }
        for index, row in df.tail(250).iterrows()  # cap the payload so it fits in the AI's context
    ]
    return {
        "symbol": ticker,
        "period": period,
        "interval": interval,
        "summary": {
            "start_close": num(df["Close"].iloc[0]),
            "end_close": num(df["Close"].iloc[-1]),
            "return_percent": pct_change(df["Close"].iloc[-1], df["Close"].iloc[0]),
            "period_high": num(df["High"].max()),
            "period_low": num(df["Low"].min()),
        },
        "candles": candles,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_technical_analysis(symbol: str, exchange: Exchange = "NSE") -> dict[str, Any]:
    """Run technical analysis on an Indian stock using one year of daily prices.

    Returns moving averages (SMA 20/50/200, EMA 20), RSI(14), MACD(12,26,9), Bollinger Bands(20,2)
    and a plain-English interpretation of each signal plus an overall trend.

    Args:
        symbol: NSE/BSE trading symbol, e.g. "INFY".
        exchange: "NSE" (default) or "BSE".
    """
    ticker = to_yahoo_symbol(symbol, exchange)
    df = await get_history(ticker, "1y", "1d")
    close = df["Close"]
    if len(close) < 50:
        raise ToolError(f"Not enough price history for {ticker} to compute indicators (need 50+ days).")

    price = close.iloc[-1]
    sma20, sma50 = close.rolling(20).mean().iloc[-1], close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    rsi14 = rsi(close).iloc[-1]
    macd_line, macd_signal, macd_hist = (s.iloc[-1] for s in macd(close))
    bb_upper, bb_middle, bb_lower = (s.iloc[-1] for s in bollinger(close))

    signals: list[str] = []
    score = 0
    if rsi14 >= 70:
        signals.append(f"RSI {rsi14:.1f} - overbought, a pullback is possible")
        score -= 1
    elif rsi14 <= 30:
        signals.append(f"RSI {rsi14:.1f} - oversold, a bounce is possible")
        score += 1
    else:
        signals.append(f"RSI {rsi14:.1f} - neutral momentum")

    if macd_line > macd_signal:
        signals.append("MACD above signal line - bullish momentum")
        score += 1
    else:
        signals.append("MACD below signal line - bearish momentum")
        score -= 1

    signals.append(f"Price {'above' if price > sma50 else 'below'} 50-day SMA")
    score += 1 if price > sma50 else -1
    if not math.isnan(sma200):
        signals.append(f"Price {'above' if price > sma200 else 'below'} 200-day SMA (long-term trend)")
        score += 1 if price > sma200 else -1
        if sma50 > sma200:
            signals.append("50-day SMA above 200-day SMA (golden-cross territory)")

    if price > bb_upper:
        signals.append("Price above upper Bollinger Band - stretched to the upside")
    elif price < bb_lower:
        signals.append("Price below lower Bollinger Band - stretched to the downside")

    trend = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    return {
        "symbol": ticker,
        "as_of": df.index[-1].date().isoformat(),
        "price": num(price),
        "moving_averages": {
            "sma_20": num(sma20),
            "sma_50": num(sma50),
            "sma_200": num(sma200),
            "ema_20": num(ema20),
        },
        "rsi_14": num(rsi14),
        "macd": {"macd": num(macd_line), "signal": num(macd_signal), "histogram": num(macd_hist)},
        "bollinger_bands": {"upper": num(bb_upper), "middle": num(bb_middle), "lower": num(bb_lower)},
        "signals": signals,
        "overall_trend": trend,
        "disclaimer": "Technical indicators describe past prices only. This is not investment advice.",
    }


@mcp.tool(annotations=READ_ONLY)
async def get_fundamentals(symbol: str, exchange: Exchange = "NSE") -> dict[str, Any]:
    """Get company fundamentals: sector, market cap (in crore), P/E, P/B, EPS, ROE, debt/equity,
    margins, growth, dividend yield and 52-week range.

    Args:
        symbol: NSE/BSE trading symbol, e.g. "HDFCBANK".
        exchange: "NSE" (default) or "BSE".
    """
    ticker = to_yahoo_symbol(symbol, exchange)
    info = await _cached(_info_cache, (ticker,), lambda: fetch_info(ticker))
    return {
        "symbol": ticker,
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap_crore": to_crore(info.get("marketCap")),
        "current_price": num(info.get("currentPrice")),
        "pe_ratio": num(info.get("trailingPE")),
        "forward_pe": num(info.get("forwardPE")),
        "price_to_book": num(info.get("priceToBook")),
        "eps": num(info.get("trailingEps")),
        "book_value": num(info.get("bookValue")),
        "roe_percent": num((info.get("returnOnEquity") or float("nan")) * 100),
        "debt_to_equity": num(info.get("debtToEquity")),
        "profit_margin_percent": num((info.get("profitMargins") or float("nan")) * 100),
        "revenue_growth_percent": num((info.get("revenueGrowth") or float("nan")) * 100),
        "dividend_yield_percent": num(info.get("dividendYield")),
        "beta": num(info.get("beta")),
        "week_52_high": num(info.get("fiftyTwoWeekHigh")),
        "week_52_low": num(info.get("fiftyTwoWeekLow")),
    }


@mcp.tool(annotations=READ_ONLY)
async def compare_stocks(symbols: list[str], exchange: Exchange = "NSE") -> dict[str, Any]:
    """Compare up to 5 Indian stocks side by side: price, today's change and 1-month/6-month/1-year returns.

    Args:
        symbols: 2 to 5 trading symbols, e.g. ["TCS", "INFY", "WIPRO"].
        exchange: "NSE" (default) or "BSE".
    """
    if not 2 <= len(symbols) <= MAX_COMPARE:
        raise ToolError(f"Provide between 2 and {MAX_COMPARE} symbols to compare.")
    tickers = list(dict.fromkeys(to_yahoo_symbol(s, exchange) for s in symbols))

    async def row(ticker: str) -> dict[str, Any]:
        try:
            quote, df = await asyncio.gather(get_quote(ticker), get_history(ticker, "1y", "1d"))
        except ToolError as exc:
            return {"symbol": ticker, "error": str(exc)}
        close = df["Close"]
        return {
            "symbol": ticker,
            "price": quote["price"],
            "change_percent_today": quote["change_percent"],
            "return_1m_percent": pct_change(close.iloc[-1], close.iloc[-min(21, len(close))]),
            "return_6m_percent": pct_change(close.iloc[-1], close.iloc[-min(126, len(close))]),
            "return_1y_percent": pct_change(close.iloc[-1], close.iloc[0]),
        }

    return {"comparison": await asyncio.gather(*(row(t) for t in tickers))}


@mcp.tool(annotations=READ_ONLY)
async def get_market_overview() -> dict[str, Any]:
    """Get a snapshot of major Indian indices: NIFTY 50, SENSEX, BANK NIFTY, sector indices and INDIA VIX."""

    async def index_row(name: str, ticker: str) -> dict[str, Any]:
        try:
            quote = await get_quote(ticker)
        except ToolError as exc:
            return {"index": name, "error": str(exc)}
        return {"index": name, "value": quote["price"], "change_percent": quote["change_percent"]}

    rows = await asyncio.gather(*(index_row(n, t) for n, t in INDICES.items()))
    return {"indices": rows, "market_status": market_status()}


def market_status() -> dict[str, Any]:
    now = datetime.now(IST)
    is_weekday = now.weekday() < 5
    is_open = is_weekday and MARKET_OPEN <= now.time() <= MARKET_CLOSE
    return {
        "is_open": is_open,
        "ist_time": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "trading_hours": "Mon-Fri 09:15-15:30 IST",
        "note": "Exchange holidays are not accounted for.",
    }


@mcp.tool(annotations=READ_ONLY)
def get_market_status() -> dict[str, Any]:
    """Check whether the NSE/BSE equity market is currently open (based on IST trading hours)."""
    return market_status()


@mcp.prompt()
def analyze_stock(symbol: str) -> str:
    """A ready-made prompt for a complete analysis of one Indian stock."""
    return (
        f"Give me a complete analysis of {symbol} on NSE. "
        "Use get_stock_quote, get_fundamentals and get_technical_analysis. "
        "Summarise valuation, financial health, trend and momentum, list key risks, "
        "and finish with a clear note that this is not investment advice."
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness probe used by the hosting platform."""
    return JSONResponse({"status": "ok"})


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=settings.host)
    logger.info("Starting india-stock-mcp on http://%s:%s/mcp", settings.host, settings.port)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
