# Prompts

The app loads these files once at startup, so restart it after editing them.

| File | Used by | Placeholders |
|---|---|---|
| `technical_system.md` | Technicals agent (TradingView) | none |
| `fundamentals_system.md` | Fundamentals & analysts agent (TradingView) | none |
| `news_system.md` | News & catalysts agent (web search + TradingView headlines) | none |
| `macro_system.md` | Sector & macro agent (web search) | none |
| `orchestrator_system.md` | Decision agent: system prompt | none |
| `research_user.md` | All research agents: per-request message | `{ticker}`, `{today}` |
| `orchestrator_user.md` | Decision agent: per-request message | `{ticker}`, `{today}`, `{reports}` (all research reports, each wrapped in `<key_report>` tags) |

Rules:
- **System prompts must not contain anything that changes between requests** (dates, tickers, data). They're prompt-cached, and any change makes every request pay full price. Per-request values belong in the `*_user.md` templates.
- In the templates, write a literal brace as `{{` or `}}`. System prompts are used as-is, so braces need no escaping there.
- Each agent's TradingView tools are listed in `RESEARCH_AGENTS` (`app/agents.py`). If you change them, update that agent's system prompt to match.
- To use a different set of prompts without editing these files, set `PROMPTS_DIR` in `.env` to another folder with the same file names.
