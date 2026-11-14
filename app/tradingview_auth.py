"""OAuth 2.1 login for TradingView's official MCP server (https://mcp.tradingview.com/mcp).

Sign in with your TradingView account (Essential plan or above) either from the web UI
("Login with TradingView" button) or from the command line:

    python -m app.tradingview_auth

Tokens are saved to .tokens/tradingview.json (gitignored) and refreshed automatically.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import ssl
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2
import truststore
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

TRADINGVIEW_MCP_URL = "https://mcp.tradingview.com/mcp"
CALLBACK_HOST, CALLBACK_PORT = "127.0.0.1", 8765
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"  # CLI login
WEB_REDIRECT_URI = "http://127.0.0.1:8081/auth/tradingview/callback"  # web UI login (app/main.py)
TOKEN_FILE = Path(__file__).resolve().parent.parent / ".tokens" / "tradingview.json"
log = logging.getLogger("trader.tradingview")
LOGIN_TIMEOUT_SECONDS = 300
USER_AGENT = "trader-app/0.1 (mcp-python-sdk)"
# Authorization server named in https://mcp.tradingview.com/.well-known/oauth-protected-resource/mcp
AUTH_SERVER_METADATA_URL = "https://www.tradingview.com/.well-known/oauth-authorization-server"
SESSION_EXPIRED = "TradingView session expired. Use \"Login with TradingView\" at the top of the page."
NOT_SIGNED_IN = "Not signed in to TradingView. Use \"Login with TradingView\" at the top of the page."
OAUTH_SCOPE = "mcp:read mcp:tools"
PENDING_LOGIN_TTL = 600  # seconds a browser sign-in may take


class LoginRequired(RuntimeError):
    pass


class _StandardHeadersTransport(httpx2.AsyncHTTPTransport):
    """The MCP SDK sends its OAuth discovery/registration requests with no User-Agent or Accept
    header, and TradingView's CDN rejects those with 403. Fill in the standard headers when missing."""

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        request.headers.setdefault("User-Agent", USER_AGENT)
        request.headers.setdefault("Accept", "application/json")
        return await super().handle_async_request(request)


class FileTokenStorage:
    """TokenStorage for the MCP SDK's OAuth provider, persisted as JSON."""

    def __init__(self, path: Path = TOKEN_FILE):
        self.path = path

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self.load().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self.load()
        data["tokens"] = tokens.model_dump(mode="json")
        data["expires_at"] = time.time() + (tokens.expires_in or 3600)
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        info = self.load().get("client_info")
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self.load()
        data["client_info"] = client_info.model_dump(mode="json")
        self._save(data)


async def _wait_for_callback() -> AuthorizationCodeResult:
    """Serve one request on the loopback redirect URI and return the authorization code."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[AuthorizationCodeResult] = loop.create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        request_line = (await reader.readline()).decode(errors="replace")
        parts = request_line.split()
        query = parse_qs(urlparse(parts[1]).query) if len(parts) > 1 else {}
        ok = "code" in query
        body = ("Signed in to TradingView. You can close this tab." if ok
                else f"TradingView sign-in failed: {query.get('error', ['no code returned'])[0]}")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()
        if result.done():
            return
        if ok:
            result.set_result(AuthorizationCodeResult(
                code=query["code"][0], state=query.get("state", [None])[0], iss=query.get("iss", [None])[0]))
        else:
            result.set_exception(LoginRequired(body))

    server = await asyncio.start_server(handle, CALLBACK_HOST, CALLBACK_PORT)
    async with server:
        return await asyncio.wait_for(result, LOGIN_TIMEOUT_SECONDS)


async def _open_browser(url: str) -> None:
    print(f"\nOpening your browser to sign in to TradingView. If it doesn't open, visit:\n{url}\n")
    webbrowser.open(url)


def _transport() -> _StandardHeadersTransport:
    # Windows certificate store, so HTTPS-inspecting antivirus or proxies are trusted the same as in the browser
    return _StandardHeadersTransport(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


async def _connect():
    """Sign in through the browser, open an authenticated MCP session, and list tools."""
    provider = OAuthClientProvider(
        server_url=TRADINGVIEW_MCP_URL,
        client_metadata=OAuthClientMetadata(
            client_name="Trader",
            redirect_uris=[REDIRECT_URI, WEB_REDIRECT_URI],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=FileTokenStorage(),
        redirect_handler=_open_browser,
        callback_handler=_wait_for_callback,
    )
    async with httpx2.AsyncClient(auth=provider, transport=_transport(), timeout=httpx2.Timeout(30, read=300)) as http:
        async with streamable_http_client(TRADINGVIEW_MCP_URL, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return (await session.list_tools()).tools


async def _refresh(data: dict) -> None:
    """Standard OAuth refresh_token grant. The SDK provider only refreshes tokens it issued in the
    same process, so after a restart it would ask for a full browser login instead."""
    refresh_token = data["tokens"].get("refresh_token")
    if not refresh_token or not data.get("client_info"):
        raise LoginRequired(SESSION_EXPIRED)
    async with httpx2.AsyncClient(transport=_transport(), timeout=30) as http:
        meta = await http.get(AUTH_SERVER_METADATA_URL)
        meta.raise_for_status()
        resp = await http.post(meta.json()["token_endpoint"], data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": data["client_info"]["client_id"],
            "resource": TRADINGVIEW_MCP_URL,
        })
    if resp.status_code >= 400:  # refresh token revoked or expired
        log.warning("TradingView token refresh rejected: HTTP %d", resp.status_code)
        raise LoginRequired(SESSION_EXPIRED)
    tokens = OAuthToken.model_validate(resp.json())
    if not tokens.refresh_token:  # server didn't rotate it; keep the old one
        tokens.refresh_token = refresh_token
    await FileTokenStorage().set_tokens(tokens)
    log.info("TradingView token refreshed (valid %ss)", tokens.expires_in)


async def get_access_token() -> str:
    """Return a valid access token, refreshing it if it's about to expire. Never opens a browser."""
    data = FileTokenStorage().load()
    if not data.get("tokens"):
        raise LoginRequired(NOT_SIGNED_IN)
    if data.get("expires_at", 0) < time.time() + 120:
        await _refresh(data)
        data = FileTokenStorage().load()
    return data["tokens"]["access_token"]


# ---- Web UI sign-in (authorization code + PKCE, redirect back to this app) -----------------


class WebLoginError(RuntimeError):
    pass


# state -> pending sign-in. In memory: the app is a single local process, and an unfinished
# sign-in simply expires.
_pending: dict[str, dict] = {}


async def _auth_metadata(http: httpx2.AsyncClient) -> dict:
    resp = await http.get(AUTH_SERVER_METADATA_URL)
    resp.raise_for_status()
    return resp.json()


async def _client_for(http: httpx2.AsyncClient, meta: dict, redirect_uri: str) -> dict:
    """Reuse the saved OAuth client if it already allows this redirect URI, else register a new one
    (allowing both the web and the CLI redirect URIs). A new client is only saved once sign-in succeeds,
    so an abandoned login never orphans the current session's refresh token."""
    info = FileTokenStorage().load().get("client_info")
    if info and redirect_uri in info.get("redirect_uris", []):
        return info
    resp = await http.post(meta["registration_endpoint"], json={
        "client_name": "Trader",
        "redirect_uris": [redirect_uri, REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    if resp.status_code >= 400:
        raise WebLoginError(f"TradingView client registration failed (HTTP {resp.status_code})")
    return resp.json()


async def start_web_login(redirect_uri: str) -> str:
    """Begin a browser sign-in. Returns the TradingView authorization URL to redirect the user to."""
    now = time.time()
    for state in [s for s, p in _pending.items() if p["created"] < now - PENDING_LOGIN_TTL]:
        del _pending[state]
    async with httpx2.AsyncClient(transport=_transport(), timeout=30) as http:
        meta = await _auth_metadata(http)
        client = await _client_for(http, meta, redirect_uri)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    _pending[state] = {"verifier": verifier, "client": client, "redirect_uri": redirect_uri,
                       "meta": meta, "created": now}
    log.info("TradingView web sign-in started")
    return meta["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": TRADINGVIEW_MCP_URL,
        "scope": OAUTH_SCOPE,
    })


async def finish_web_login(code: str | None, state: str | None, iss: str | None = None) -> None:
    """Handle the redirect back from TradingView: check state, exchange the code, save the tokens."""
    pending = _pending.pop(state or "", None)
    if pending is None or pending["created"] < time.time() - PENDING_LOGIN_TTL:
        raise WebLoginError("Sign-in link expired or was already used. Click Login with TradingView again.")
    if iss and iss.rstrip("/") != pending["meta"]["issuer"].rstrip("/"):  # RFC 9207 mix-up protection
        raise WebLoginError("Sign-in response came from an unexpected server.")
    if not code:
        raise WebLoginError("TradingView didn't return an authorization code.")
    async with httpx2.AsyncClient(transport=_transport(), timeout=30) as http:
        resp = await http.post(pending["meta"]["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": pending["client"]["client_id"],
            "code_verifier": pending["verifier"],
            "resource": TRADINGVIEW_MCP_URL,
        })
    if resp.status_code >= 400:
        log.warning("TradingView code exchange rejected: HTTP %d", resp.status_code)
        raise WebLoginError(f"TradingView rejected the sign-in (HTTP {resp.status_code}). Try again.")
    storage = FileTokenStorage()
    await storage.set_client_info(OAuthClientInformationFull.model_validate(pending["client"]))
    await storage.set_tokens(OAuthToken.model_validate(resp.json()))
    log.info("TradingView web sign-in completed")


async def logout() -> None:
    """Revoke the tokens at TradingView (best effort) and delete them locally. Keeps the client registration."""
    data = FileTokenStorage().load()
    tokens, client = data.get("tokens") or {}, data.get("client_info") or {}
    try:
        async with httpx2.AsyncClient(transport=_transport(), timeout=15) as http:
            endpoint = (await _auth_metadata(http)).get("revocation_endpoint")
            for kind in ("refresh_token", "access_token"):
                if endpoint and tokens.get(kind):
                    await http.post(endpoint, data={"token": tokens[kind], "token_type_hint": kind,
                                                    "client_id": client.get("client_id", "")})
    except httpx2.HTTPError:
        log.warning("TradingView token revocation failed; deleting local tokens anyway", exc_info=True)
    data.pop("tokens", None)
    data.pop("expires_at", None)
    FileTokenStorage()._save(data)
    log.info("Signed out of TradingView")


def status() -> dict:
    """Whether the app holds a usable TradingView session (an expired access token is fine if it can refresh)."""
    data = FileTokenStorage().load()
    tokens = data.get("tokens") or {}
    connected = bool(tokens.get("refresh_token")) or data.get("expires_at", 0) > time.time()
    return {"connected": connected}


async def _login() -> None:
    tools = await _connect()
    print(f"Connected to TradingView MCP. {len(tools)} tools available:")
    for t in tools:
        print(f"  - {t.name}")
    print(f"\nTokens saved to {TOKEN_FILE}")


if __name__ == "__main__":
    asyncio.run(_login())
