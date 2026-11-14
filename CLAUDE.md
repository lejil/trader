# Trader

Project instructions for Claude Code. Keep this file short and current — it is loaded into every session.

## Overview
Web app: the user enters a stock ticker, and four specialist research subagents run in parallel (technicals and fundamentals via TradingView's official MCP; news via web search + TradingView headlines; sector & macro via web search). A decision agent then returns BUY / WAIT / AVOID with an entry zone, stop loss, target, and the next best time to buy.

## Tech stack
Python 3.10+, FastAPI, Anthropic Python SDK (`anthropic[mcp]`), research agents on `claude-haiku-5-5` (`CLAUDE_MODEL`), decision step on `claude-sonnet-5-5` (`CLAUDE_DECISION_MODEL`). `app/agents.py` switches the web search version and refusal fallbacks by research model. TradingView's official MCP server (`https://mcp.tradingview.com/mcp`) is called through Anthropic's MCP connector (beta `mcp-client-2025-11-20`) and authenticated with OAuth 2.1 (`app/tradingview_auth.py`).

## Commands
- Install: `uv pip install --system-certs --python .\.venv\Scripts\python.exe -r requirements.txt` (plain pip fails here because of HTTPS interception)
- TradingView sign-in (once): `python -m app.tradingview_auth`
- Run: `python -m app.main` → http://127.0.0.1:8081 (don't use `uvicorn --reload` on Windows; it breaks the MCP subprocess)
- Test: `TODO`

## Project layout
- `app/agents.py`: `RESEARCH_AGENTS` registry (each agent's TradingView tool allowlist and/or web search), `run_research_agent` (streamed, emits UI events), `orchestrate` (structured output), `analyze` (runs all research agents in parallel, then the decision). Adding an agent = new `ResearchAgent` entry + `prompts/<key>_system.md` + entry in `app/prompts.py` REQUIRED; the UI picks it up automatically.
- `docs/architecture.drawio`: architecture diagram. Keep it in sync when agents or data flow change.
- `app/tradingview_auth.py`: OAuth: web sign-in (`start_web_login` / `finish_web_login`, routes `/auth/tradingview/login` + `/callback` in `app/main.py`), `logout`, `status`, CLI login (`python -m app.tradingview_auth`), and `get_access_token()` with refresh. Tokens live in `.tokens/`; never read or print them.
- Prompt caching: system prompts and tool lists must stay byte-identical across requests. Put anything that varies (ticker, date, data) in the `prompts/*_user.md` templates, never in `prompts/*_system.md`. Check `cache_read` in the logs after changes.
- Never enable the TradingView watchlist or alert tools (they write to the user's account) without asking.
- `prompts/*.md`: all agent prompts (system prompts + per-request templates), loaded at startup by `app/prompts.py`. See `prompts/README.md`. Edit prompts there, never inline in Python.
- `app/models.py`: Pydantic `Recommendation` schema. Keep it flat (no nested models).
- `app/main.py`: FastAPI routes and ticker validation
- `app/static/index.html`: single-page UI

## Conventions
- Never hardcode API keys or secrets; read them from environment variables or a `.env` file (gitignored).
- Default to paper/sandbox trading environments. Never place live orders without explicit confirmation.
- Use decimal types (not floats) for prices, quantities, and money.
- Keep strategy logic separate from broker/exchange I/O so it can be backtested.
