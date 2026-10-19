# 🇮🇳 India Stock MCP Server

A production-ready [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives AI assistants
like Claude, Cursor and VS Code Copilot live access to **Indian stock market data** from **NSE** and **BSE**:
quotes, price history, technical indicators, fundamentals and market indices.

It runs over **Streamable HTTP**, so anyone can connect to the hosted server with just a URL. Nothing to install.

> ⚠️ **Not investment advice.** This project is for education and research. Data may be delayed (~15 minutes)
> and may contain errors. Always do your own research before making investment decisions.

---

## ✨ Tools

| Tool | What it does | Example question to your AI |
|---|---|---|
| `get_stock_quote` | Latest price, change %, day range, volume | "What's Reliance trading at?" |
| `get_price_history` | OHLCV candles (1mo–5y, daily/weekly/monthly) + period return | "How has Tata Motors done this year?" |
| `get_technical_analysis` | SMA 20/50/200, EMA 20, RSI(14), MACD, Bollinger Bands + plain-English signals | "Is Infosys overbought?" |
| `get_fundamentals` | Sector, market cap (₹ crore), P/E, P/B, EPS, ROE, debt/equity, margins, dividend yield | "Is HDFC Bank cheap compared to its book value?" |
| `compare_stocks` | Up to 5 stocks side by side: today, 1M, 6M and 1Y returns | "Compare TCS, Infosys and Wipro" |
| `get_market_overview` | NIFTY 50, SENSEX, BANK NIFTY, sector indices, INDIA VIX | "How is the market doing today?" |
| `get_market_status` | Whether NSE/BSE is open right now (IST) | "Is the market open?" |

There is also a prompt, **`analyze_stock`**, that runs a complete analysis of one stock.

Symbols are NSE trading symbols such as `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, `M&M` or `BAJAJ-AUTO`.

---

## 🚀 Connect to the hosted server

**Server URL:** `https://YOUR-APP.onrender.com/mcp`

<details>
<summary><b>Claude (claude.ai / Claude Desktop)</b></summary>

Settings → Connectors → **Add custom connector** → paste the server URL.
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add --transport http india-stocks https://YOUR-APP.onrender.com/mcp
```
</details>

<details>
<summary><b>Cursor</b> (<code>~/.cursor/mcp.json</code>)</summary>

```json
{
  "mcpServers": {
    "india-stocks": { "url": "https://YOUR-APP.onrender.com/mcp" }
  }
}
```
</details>

<details>
<summary><b>VS Code</b> (<code>.vscode/mcp.json</code>)</summary>

```json
{
  "servers": {
    "india-stocks": { "type": "http", "url": "https://YOUR-APP.onrender.com/mcp" }
  }
}
```
</details>

> The free hosting tier sleeps when idle, so the first request after a quiet period can take ~30–60 seconds.

---

## 🏗️ Architecture

```
AI client ──HTTPS POST /mcp──▶ Rate limiter ──▶ MCP server (stateless) ──▶ Tool
                                (60 req/min/IP)                              │
                                                       TTL cache ◀──────────┤
                                                           │ miss            │
                                                           ▼                 │
                                                   Yahoo Finance ──(retry)──┘
```

- **Stateless Streamable HTTP**: every request is independent, so the server scales horizontally.
- **TTL caching**: quotes 1 min, price history 15 min, fundamentals 6 h. Popular stocks cost one upstream call.
- **Retries with backoff**: transient Yahoo failures are retried; real outages return a clear message.
- **Per-client rate limiting**: token bucket keyed on client IP, resistant to forged `X-Forwarded-For` headers.
- **Input validation**: symbols, periods and intervals are validated before any network call.

---

## 💻 Run it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/india-stock-mcp.git
cd india-stock-mcp
uv sync
uv run python -m india_stock_mcp.server
```

The server starts at `http://127.0.0.1:8000/mcp`. Check it with:

```bash
curl http://127.0.0.1:8000/health          # {"status":"ok"}
uv run python scripts/smoke_test.py        # calls every tool
```

### With Docker

```bash
docker build -t india-stock-mcp .
docker run -d --name india-stock-mcp -p 8000:8000 india-stock-mcp
```

---

## ⚙️ Configuration

All settings are environment variables (or a `.env` file; see [`.env.example`](.env.example)).

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Interface to listen on |
| `PORT` | `8000` | Port to listen on |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `QUOTE_CACHE_TTL` | `60` | Seconds to cache quotes |
| `HISTORY_CACHE_TTL` | `900` | Seconds to cache price history |
| `INFO_CACHE_TTL` | `21600` | Seconds to cache fundamentals |
| `RATE_LIMIT_PER_MINUTE` | `60` | Max MCP requests per minute per client IP |
| `TRUSTED_PROXY_HOPS` | `0` | Load balancers in front of the app (`1` on Render) |

---

## 🧪 Development

```bash
uv sync                                   # installs dev tools too
uv run pytest                             # 36 offline tests (Yahoo is mocked)
uv run ruff check src scripts tests       # lint
uv run ruff format src scripts tests      # format
```

### Project structure

```
src/india_stock_mcp/
├── server.py      # MCP server, tools, data fetching, indicators
├── config.py      # settings from environment variables
└── ratelimit.py   # per-client rate limiting middleware
tests/             # offline test suite
scripts/           # smoke test against a running server
Dockerfile         # multi-stage, non-root production image
```

---

## ⚠️ Limitations

- **Data source:** prices come from Yahoo Finance via [`yfinance`](https://github.com/ranaroussi/yfinance), an
  unofficial API intended for personal and research use. Data can be delayed or temporarily unavailable.
  For commercial use, switch to a licensed provider (e.g. a broker API).
- **BSE:** use trading symbols with `exchange="BSE"` (e.g. `RELIANCE`), not numeric scrip codes. Yahoo's BSE
  history is often limited, so previous close and technical analysis work best on NSE.
- **Market status** uses regular trading hours (Mon–Fri 09:15–15:30 IST) and does not know exchange holidays.
- **Rate limits** are per server instance (in memory).

---

## 📄 License

[MIT](LICENSE) © 2026 DiwakarReddy

**Disclaimer:** This software is provided for informational and educational purposes only and does not
constitute financial, investment or trading advice. The authors accept no liability for any losses arising
from its use.
