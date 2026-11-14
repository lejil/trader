import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlencode

import anthropic
import httpx2
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import tradingview_auth as tv_auth  # noqa: E402
from .agents import analyze  # noqa: E402
from .logging_config import setup_logging  # noqa: E402
from .models import AnalysisResponse  # noqa: E402
from .tradingview_auth import LoginRequired  # noqa: E402

setup_logging()
log = logging.getLogger("trader.api")

STATIC = Path(__file__).parent / "static"
# Tickers only, e.g. AAPL, BRK.B, RELIANCE.NS, NASDAQ:MSFT - also keeps free text out of the prompts
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-:]{0,19}$")
INVALID_TICKER = "Enter a valid ticker symbol, e.g. AAPL or NASDAQ:MSFT"

app = FastAPI(title="Stock Analyzer")


class AnalyzeRequest(BaseModel):
    ticker: str


def _normalize(raw: str) -> str | None:
    ticker = raw.strip().upper()
    if TICKER_RE.match(ticker):
        return ticker
    log.warning("rejected invalid ticker input (%d chars)", len(raw))
    return None


def _describe_error(ticker: str, e: Exception) -> tuple[int, str]:
    """Log an analysis failure and turn it into an HTTP status + user-facing message."""
    if isinstance(e, LoginRequired):
        log.warning("%s: %s", ticker, e)
        return 401, str(e)
    if isinstance(e, httpx2.HTTPError):  # TradingView token refresh couldn't connect
        log.exception("%s: TradingView connection failed", ticker)
        return 502, (f"Could not reach TradingView ({type(e).__name__}). "
                     "Check that Norton isn't scanning tradingview.com.")
    if isinstance(e, anthropic.RateLimitError):
        log.warning("%s: Claude API rate limited (request_id=%s)", ticker, e.request_id)
        return 429, "Rate limited by the Claude API. Try again in a minute."
    if isinstance(e, anthropic.APIStatusError):
        log.error("%s: Claude API error %s (request_id=%s): %s", ticker, e.status_code, e.request_id, e.message)
        return 502, f"Claude API error: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        log.exception("%s: could not reach the Claude API", ticker)
        return 502, "Could not reach the Claude API."
    log.exception("%s: analysis failed", ticker)
    return 500, str(e) if isinstance(e, RuntimeError) else f"Unexpected error: {type(e).__name__}"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---- TradingView sign-in --------------------------------------------------------------------

def _home(**params: str) -> RedirectResponse:
    return RedirectResponse("/?" + urlencode(params), status_code=303)


@app.get("/auth/tradingview/login")
async def tradingview_login(request: Request):
    """Start OAuth: send the browser to TradingView's sign-in page."""
    try:
        url = await tv_auth.start_web_login(str(request.url_for("tradingview_callback")))
    except (httpx2.HTTPError, tv_auth.WebLoginError) as e:
        log.exception("TradingView sign-in could not start")
        return _home(tv_error=f"Could not reach TradingView ({type(e).__name__}). "
                              "Check that Norton isn't scanning tradingview.com.")
    return RedirectResponse(url, status_code=303)


@app.get("/auth/tradingview/callback", name="tradingview_callback")
async def tradingview_callback(code: str | None = None, state: str | None = None,
                               error: str | None = None, error_description: str | None = None,
                               iss: str | None = None):
    """TradingView redirects here after sign-in."""
    if error:
        tv_auth._pending.pop(state or "", None)
        log.warning("TradingView sign-in returned error: %s", error)
        return _home(tv_error="Sign-in was cancelled." if error == "access_denied"
                     else f"TradingView sign-in failed: {error_description or error}")
    try:
        await tv_auth.finish_web_login(code, state, iss)
    except tv_auth.WebLoginError as e:
        return _home(tv_error=str(e))
    except httpx2.HTTPError as e:
        log.exception("TradingView code exchange failed")
        return _home(tv_error=f"Could not reach TradingView ({type(e).__name__}).")
    return _home(tv="connected")


@app.get("/api/tradingview/status")
def tradingview_status():
    return tv_auth.status()


@app.post("/api/tradingview/logout")
async def tradingview_logout():
    await tv_auth.logout()
    return tv_auth.status()


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze_ticker(req: AnalyzeRequest):
    """Plain JSON API: waits for the full analysis."""
    ticker = _normalize(req.ticker)
    if ticker is None:
        raise HTTPException(400, INVALID_TICKER)
    try:
        return await analyze(ticker)
    except Exception as e:
        status, message = _describe_error(ticker, e)
        raise HTTPException(status, message)


@app.get("/api/analyze/stream")
async def analyze_stream(ticker: str):
    """Server-sent events: live agent/tool progress, then a final `result` or `error` event."""
    normalized = _normalize(ticker)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def run():
        try:
            if normalized is None:
                await queue.put({"type": "error", "message": INVALID_TICKER})
                return
            result = await analyze(normalized, queue.put)
            await queue.put({"type": "result", "data": result.model_dump(mode="json")})
        except Exception as e:
            _, message = _describe_error(normalized, e)
            await queue.put({"type": "error", "message": message, "login_required": isinstance(e, LoginRequired)})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while (event := await queue.get()) is not None:
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Browser closed the page or the stream: stop the agents so they don't keep spending tokens
            if not task.done():
                log.info("%s: client disconnected, cancelling analysis", normalized)
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    # No --reload: on Windows the reloader switches to an event loop that can't spawn the MCP subprocess.
    # log_config=None keeps uvicorn's own logs flowing through our handlers (console + logs/trader.log).
    uvicorn.run(app, host="127.0.0.1", port=8081, log_config=None)
