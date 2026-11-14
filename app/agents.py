"""Specialist research subagents (TradingView MCP and/or web search) run in parallel, then a decision
agent combines their reports into a recommendation."""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

import anthropic

from . import prompts
from .models import AnalysisResponse, Recommendation
from .tradingview_auth import TRADINGVIEW_MCP_URL, get_access_token

log = logging.getLogger("trader.agents")

# Progress callback for the live UI; receives small JSON-able dicts
Emit = Callable[[dict], Awaitable[None]]

# Research agents gather data (cheap, fast model); the decision step weighs it (stronger model)
RESEARCH_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
DECISION_MODEL = os.getenv("CLAUDE_DECISION_MODEL", "claude-sonnet-5-5")
IS_HAIKU = "haiku" in RESEARCH_MODEL
# Dynamic-filtering web search (_20260209) is for Opus/Sonnet; Haiku uses the basic version
WEB_SEARCH_TOOL = "web_search_20250305" if IS_HAIKU else "web_search_20260209"
# Server-side refusal fallback to another model; Haiku has none, so the parameter isn't sent
FALLBACK_BETA = None if IS_HAIKU else "server-side-fallback-2026-07-01"
MAX_PAUSE_CONTINUATIONS = 5


@dataclass(frozen=True)
class ResearchAgent:
    key: str                       # id used in events, logs, prompt file name (prompts/<key>_system.md)
    title: str                     # UI column title
    source: str                    # UI subtitle
    tradingview_tools: tuple = ()  # read-only TradingView MCP tools this agent may call
    web_search: bool = False
    max_searches: int = 6


# All research agents run in parallel. TradingView's watchlist and alert tools modify the user's
# account and are never listed here, so they stay disabled. To add an agent: add an entry and a
# prompts/<key>_system.md file (and list it in app/prompts.py REQUIRED).
RESEARCH_AGENTS = (
    ResearchAgent(
        "technical", "Technicals", "TradingView",
        tradingview_tools=("mcp-tv-search-symbols", "mcp-tv-get-technicals-rating",
                           "mcp-tv-get-ohlcv", "mcp-tv-get-symbol-data"),
    ),
    ResearchAgent(
        "fundamentals", "Fundamentals & analysts", "TradingView",
        tradingview_tools=("mcp-tv-search-symbols", "mcp-tv-get-financials", "mcp-tv-get-forecasts",
                           "mcp-tv-get-earnings-calendar", "mcp-tv-get-symbol-data"),
    ),
    ResearchAgent(
        "news", "News & catalysts", "Web search + TradingView headlines",
        tradingview_tools=("mcp-tv-search-symbols", "mcp-tv-get-news", "mcp-tv-get-news-story"),
        web_search=True, max_searches=6,
    ),
    ResearchAgent(
        "macro", "Sector & macro", "Web search",
        web_search=True, max_searches=5,
    ),
)
DECISION_AGENT = {"key": "orchestrator", "title": "Decision", "source": "Combines all reports"}


def _cached_system(prompt: str) -> list[dict]:
    """System prompt with a cache breakpoint at its end. Tools render before system, so this caches
    the tool definitions + instructions, which are identical for every ticker. The ticker and date
    go in the user message, after the breakpoint, so they don't invalidate it."""
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]


def _text(message) -> str:
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


def _log_response(agent: str, ticker: str, response, started: float) -> None:
    tools = [b.name for b in response.content if b.type in ("mcp_tool_use", "server_tool_use")]
    usage = response.usage
    log.info("%s %s: %.1fs, model=%s, stop=%s, tokens in=%d out=%d cache_read=%d cache_write=%d, tool calls=%d %s",
             agent, ticker, time.monotonic() - started, response.model, response.stop_reason,
             usage.input_tokens, usage.output_tokens,
             usage.cache_read_input_tokens or 0, usage.cache_creation_input_tokens or 0,
             len(tools), tools or "")
    errors = [b for b in response.content if b.type.endswith("tool_result") and _is_error_result(b)]
    if errors:
        log.warning("%s %s: %d tool call(s) returned errors", agent, ticker, len(errors))


def _summarize_input(tool_input) -> str:
    """Short one-line view of a tool call's arguments for the UI, e.g. symbol=NASDAQ:AAPL, interval=1D."""
    if not isinstance(tool_input, dict):
        return ""
    parts = [f"{k}={v}" for k, v in tool_input.items() if isinstance(v, (str, int, float, bool))]
    text = ", ".join(parts)
    return text if len(text) <= 120 else text[:117] + "..."


def _is_error_result(block) -> bool:
    if getattr(block, "is_error", False):  # MCP tool results
        return True
    content = getattr(block, "content", None)  # server tool results carry an error object instead of a list
    return str(getattr(content, "type", "")).endswith("error")


def _request_params(agent: ResearchAgent, token: str) -> dict:
    """API parameters for one research agent: its prompt, plus TradingView and/or web search tools."""
    tools, betas, extra = [], [], {}
    if agent.tradingview_tools:
        betas.append("mcp-client-2025-11-20")
        extra["mcp_servers"] = [{
            "type": "url",
            "url": TRADINGVIEW_MCP_URL,
            "name": "tradingview",
            "authorization_token": token,
        }]
        tools.append({
            "type": "mcp_toolset",
            "mcp_server_name": "tradingview",
            "default_config": {"enabled": False},
            "configs": {name: {"enabled": True} for name in agent.tradingview_tools},
        })
    if agent.web_search:
        tools.append({"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": agent.max_searches})
    if FALLBACK_BETA:
        betas.append(FALLBACK_BETA)
        extra["fallbacks"] = "default"
    if betas:
        extra["betas"] = betas
    return dict(
        model=RESEARCH_MODEL,
        max_tokens=16000,
        system=_cached_system(prompts.system(agent.key)),
        # Automatic caching on the latest block, so each server-side tool step and any
        # pause_turn continuation reuses the turn so far
        cache_control={"type": "ephemeral"},
        tools=tools,
        output_config={"effort": "medium"},
        **extra,
    )


async def _stream_turn(client: anthropic.AsyncAnthropic, agent: str, ticker: str, emit: Emit, **params):
    """One streamed API call. Reports each tool call as it starts and finishes, returns the final message."""
    started = time.monotonic()
    async with client.beta.messages.stream(**params) as stream:
        async for event in stream:
            if event.type != "content_block_stop":
                continue
            block = event.content_block
            if block.type in ("mcp_tool_use", "server_tool_use"):
                await emit({"type": "tool_start", "agent": agent, "id": block.id,
                            "tool": block.name, "input": _summarize_input(block.input)})
            elif block.type.endswith("tool_result") and getattr(block, "tool_use_id", None):
                await emit({"type": "tool_end", "agent": agent, "id": block.tool_use_id,
                            "ok": not _is_error_result(block)})
        response = await stream.get_final_message()
    _log_response(agent, ticker, response, started)
    return response


async def run_research_agent(client: anthropic.AsyncAnthropic, agent: ResearchAgent, ticker: str,
                             token: str, emit: Emit) -> str:
    """Streamed agent turn, resending on pause_turn so long server-side tool loops can finish."""
    log.info("%s %s: started", agent.key, ticker)
    await emit({"type": "agent_start", "agent": agent.key})
    params = _request_params(agent, token)
    user_msg = {"role": "user", "content": prompts.render("research_user", ticker=ticker, today=str(date.today()))}
    messages = [user_msg]
    for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
        response = await _stream_turn(client, agent.key, ticker, emit, messages=messages, **params)
        if response.stop_reason != "pause_turn":
            break
        messages = [user_msg, {"role": "assistant", "content": response.content}]
    if response.stop_reason == "refusal":
        log.warning("%s %s: refused", agent.key, ticker)
        await emit({"type": "agent_end", "agent": agent.key, "ok": False, "message": "Declined the request"})
        return f"The {agent.title} agent declined this request."
    await emit({"type": "agent_end", "agent": agent.key, "ok": True})
    return _text(response)


async def orchestrate(client: anthropic.AsyncAnthropic, ticker: str, reports: dict[str, str],
                      emit: Emit) -> Recommendation:
    key = DECISION_AGENT["key"]
    log.info("%s %s: started", key, ticker)
    await emit({"type": "agent_start", "agent": key})
    started = time.monotonic()
    report_text = "\n\n".join(
        f"<{agent.key}_report title=\"{agent.title}\">\n{reports[agent.key]}\n</{agent.key}_report>"
        for agent in RESEARCH_AGENTS
    )
    response = await client.messages.parse(
        model=DECISION_MODEL,
        max_tokens=16000,
        system=_cached_system(prompts.system(key)),
        output_config={"effort": "high"},
        messages=[{
            "role": "user",
            "content": prompts.render("orchestrator_user", ticker=ticker, today=str(date.today()),
                                      reports=report_text),
        }],
        output_format=Recommendation,
    )
    _log_response(key, ticker, response, started)
    if response.stop_reason == "refusal" or response.parsed_output is None:
        await emit({"type": "agent_end", "agent": key, "ok": False, "message": "No recommendation"})
        raise RuntimeError(f"Orchestrator returned no recommendation (stop_reason={response.stop_reason})")
    await emit({"type": "agent_end", "agent": key, "ok": True})
    return response.parsed_output


async def _no_events(event: dict) -> None:
    pass


async def analyze(ticker: str, emit: Emit = _no_events) -> AnalysisResponse:
    """Run all research agents in parallel, then the decision agent. `emit` receives progress events
    (plan / agent_start / agent_end / tool_start / tool_end) for the live UI."""
    started = time.monotonic()
    log.info("analyze %s: %d research agents on %s, decision on %s",
             ticker, len(RESEARCH_AGENTS), RESEARCH_MODEL, DECISION_MODEL)
    # Fail fast with LoginRequired before spending anything on the agents
    token = await get_access_token()
    await emit({"type": "plan", "agents": [
        *({"key": a.key, "title": a.title, "source": a.source} for a in RESEARCH_AGENTS),
        DECISION_AGENT,
    ]})
    client = anthropic.AsyncAnthropic()
    results = await asyncio.gather(
        *(run_research_agent(client, agent, ticker, token, emit) for agent in RESEARCH_AGENTS),
        return_exceptions=True,
    )
    # A failed agent shouldn't sink the whole analysis; the decision agent lowers confidence instead
    reports = {}
    for agent, result in zip(RESEARCH_AGENTS, results):
        if isinstance(result, BaseException):
            log.error("%s agent %s failed", agent.key, ticker, exc_info=result)
            await emit({"type": "agent_end", "agent": agent.key, "ok": False, "message": type(result).__name__})
            result = f"{agent.title} data unavailable: {result!r}"
        reports[agent.key] = result
    recommendation = await orchestrate(client, ticker, reports, emit)
    log.info("analyze %s: %s (%s confidence) in %.1fs", ticker, recommendation.verdict,
             recommendation.confidence, time.monotonic() - started)
    return AnalysisResponse(
        recommendation=recommendation,
        reports=[{"key": a.key, "title": a.title, "text": reports[a.key]} for a in RESEARCH_AGENTS],
    )
