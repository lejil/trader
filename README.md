# Trader

Enter a stock ticker and get an AI buy / wait / avoid call, with entry zone, stop loss, target, and the next best time to buy.

```
            ┌─ Technicals ─────────── TradingView MCP ────────────┐
            ├─ Fundamentals & analysts ─ TradingView MCP ─────────┤
ticker ──►  ├─ News & catalysts ───── web search + TV headlines ───┼─► Decision (Sonnet 5.5) ─► BUY / WAIT / AVOID
            └─ Sector & macro ─────── web search ─────────────────┘     (structured JSON)
                 4 research agents on Haiku 5.5, all in parallel
```

The four research agents run in parallel. If one fails, the decision agent still answers, at lower confidence. The full architecture diagram is in [`docs/architecture.drawio`](docs/architecture.drawio): open it at [app.diagrams.net](https://app.diagrams.net) or with the VS Code Draw.io extension.

## TradingView MCP

- Server: `https://mcp.tradingview.com/mcp` ([docs](https://www.tradingview.com/mcp/docs)). It's in beta and needs a TradingView **Essential** plan or above; trial plans don't include it.
- Auth: OAuth 2.1 (authorization code + PKCE) through a browser sign-in. Click **Login with TradingView** at the top of the app, or run `python -m app.tradingview_auth`. Tokens are saved to `.tokens/tradingview.json`, which is gitignored, and refresh automatically. **Sign out** revokes them at TradingView and deletes them locally.
- Claude calls the server through Anthropic's MCP connector, so the tool calls run on Anthropic's side. Each agent can use only its own **read-only** tools, listed in `RESEARCH_AGENTS` in `app/agents.py`. Nine are used in total: `mcp-tv-search-symbols`, `mcp-tv-get-ohlcv`, `mcp-tv-get-technicals-rating`, `mcp-tv-get-symbol-data`, `mcp-tv-get-forecasts`, `mcp-tv-get-financials`, `mcp-tv-get-earnings-calendar`, `mcp-tv-get-news` and `mcp-tv-get-news-story`. The server's real tool names have an `mcp-tv-` prefix; the docs list them without it. The watchlist and alert tools that change your account are disabled.
- Rate limit: about 100 tool calls per minute per user.

## Setup

```powershell
python -m venv .venv
uv pip install --system-certs --python .\.venv\Scripts\python.exe -r requirements.txt
copy .env.example .env   # add ANTHROPIC_API_KEY unless it's already set in your environment
```

On this machine, plain `pip install` fails because HTTPS traffic is intercepted. `uv --system-certs` trusts the Windows certificate store and works.

## Run

```powershell
.\.venv\Scripts\python -m app.main
```

Open http://127.0.0.1:8081. One analysis takes about 1–2 minutes.

## Troubleshooting

- **"Not signed in" / "session expired"**: click **Login with TradingView** at the top of the page.
- **Sign-in hangs or fails with "peer closed connection" / `IncompleteRead`**: something on your machine is cutting off responses from `mcp.tradingview.com`. Norton 360 and McAfee web protection both do HTTPS scanning. Add an exclusion for `mcp.tradingview.com` (or for `python.exe`), or turn off HTTPS scanning, then retry. Check with:
  `curl.exe -s -w "%{http_code}\n" https://mcp.tradingview.com/.well-known/oauth-protected-resource/mcp`. A healthy connection prints a JSON body followed by `200`.

## Disclaimer

The analysis is AI-generated, for education only, and is not financial advice.
