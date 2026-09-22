"""How a CLI gets a token, and where it keeps it.

The BFF accepts ``Authorization: Bearer <Cognito access token>`` and holds nothing
for it. So the CLI obtains the token itself:

1. `login` starts a listener on ``127.0.0.1:<port>``, opens the hosted UI with PKCE
   and exchanges the code **without a client secret** — it is a public client.
2. Tokens go into the OS keyring (the ``keyring`` package; a ``0600`` file when
   there is none), one entry per profile.
3. `TokenProvider` refreshes when within a minute of expiry — the same margin the
   BFF uses for browser sessions.
4. ``COHO_ACCESS_TOKEN``, if set, wins over the store. For CI; it carries a person's
   identity and is a stopgap until service accounts exist.

⚠️ Cognito requires the callback URL to match **exactly**, port included, so the
app client must be registered with ``http://127.0.0.1:<callback_port>/callback``
and the profile must use the same port. There is no dynamic-port loopback rule as
in RFC 8252 §7.3.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .errors import AuthError, NotLoggedIn
from .profiles import Profile, config_dir, restrict_to_owner

ENV_ACCESS_TOKEN = "COHO_ACCESS_TOKEN"
REFRESH_MARGIN_SECONDS = 60
KEYRING_SERVICE = "coho"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TokenSet:
    """What the provider handed back, plus when the access token stops working."""

    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    expires_at: float = 0.0
    """Unix seconds. ``0`` means unknown — treated as never expiring, never refreshed."""
    token_type: str = "Bearer"

    @property
    def expires_in(self) -> float | None:
        return None if not self.expires_at else self.expires_at - time.time()

    def needs_refresh(self, margin: float = REFRESH_MARGIN_SECONDS) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at - margin

    @classmethod
    def from_token_response(
        cls, body: dict[str, Any], *, refresh_token: str | None = None
    ) -> TokenSet:
        expires_in = body.get("expires_in")
        return cls(
            access_token=str(body["access_token"]),
            refresh_token=body.get("refresh_token") or refresh_token,
            id_token=body.get("id_token"),
            expires_at=time.time() + float(expires_in) if expires_in else 0.0,
            token_type=str(body.get("token_type", "Bearer")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TokenSet:
        return cls(
            access_token=str(raw["access_token"]),
            refresh_token=raw.get("refresh_token"),
            id_token=raw.get("id_token"),
            expires_at=float(raw.get("expires_at") or 0.0),
            token_type=str(raw.get("token_type", "Bearer")),
        )


def claims(jwt: str) -> dict[str, Any]:
    """The payload of a JWT, **unverified** — for showing ``sub``/``exp``, never for trust."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class TokenStore:
    """Where a profile's tokens live. Subclasses implement three methods."""

    def load(self, profile: str) -> TokenSet | None:
        raise NotImplementedError

    def save(self, profile: str, tokens: TokenSet) -> None:
        raise NotImplementedError

    def delete(self, profile: str) -> None:
        raise NotImplementedError


class FileTokenStore(TokenStore):
    """``<config dir>/credentials.json``, mode ``0600``. The fallback when there is no keyring."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "credentials.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            restrict_to_owner(fh.fileno())
            json.dump(data, fh)
        tmp.replace(self.path)

    def load(self, profile: str) -> TokenSet | None:
        raw = self._read().get(profile)
        return TokenSet.from_dict(raw) if isinstance(raw, dict) else None

    def save(self, profile: str, tokens: TokenSet) -> None:
        data = self._read()
        data[profile] = tokens.to_dict()
        self._write(data)

    def delete(self, profile: str) -> None:
        data = self._read()
        if profile in data:
            del data[profile]
            self._write(data)


class KeyringTokenStore(TokenStore):
    """The OS keyring, via the optional ``keyring`` package. One JSON blob per profile."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise AuthError(
                "the 'keyring' package is not installed; use token_store = \"file\""
            ) from exc
        self._keyring = keyring
        self._error = KeyringError
        self.service = service

    def load(self, profile: str) -> TokenSet | None:
        try:
            raw = self._keyring.get_password(self.service, profile)
        except self._error as exc:
            raise AuthError(f"keyring read failed: {exc}") from exc
        if not raw:
            return None
        try:
            return TokenSet.from_dict(json.loads(raw))
        except (ValueError, KeyError):
            return None

    def save(self, profile: str, tokens: TokenSet) -> None:
        try:
            self._keyring.set_password(self.service, profile, json.dumps(tokens.to_dict()))
        except self._error as exc:
            raise AuthError(f"keyring write failed: {exc}") from exc

    def delete(self, profile: str) -> None:
        with contextlib.suppress(self._error):
            self._keyring.delete_password(self.service, profile)


def token_store_for(profile: Profile) -> TokenStore:
    """The store the profile asks for, falling back to the file when the keyring is unusable."""
    if profile.token_store == "file":
        return FileTokenStore()
    try:
        store = KeyringTokenStore()
        # Probe: a backend that raises on read (headless Linux, no D-Bus) should not
        # be discovered on the first real call.
        store.load("__probe__")
        return store
    except AuthError:
        return FileTokenStore()


# ---------------------------------------------------------------------------
# PKCE login
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


_STYLE = b"body{font-family:system-ui,sans-serif;margin:4rem auto;max-width:32rem;color:#222}"
_SUCCESS_PAGE = (
    b'<!doctype html><html><head><meta charset="utf-8"><title>coho</title><style>'
    + _STYLE
    + b"</style></head><body><h1>Signed in</h1>"
    b"<p>You can close this window and return to the terminal.</p></body></html>"
)

_FAILURE_PAGE = (
    b'<!doctype html><html><head><meta charset="utf-8"><title>coho</title><style>'
    + _STYLE
    + b"</style></head><body><h1>Sign-in failed</h1>"
    b"<p>Return to the terminal for the reason.</p></body></html>"
)


class _CallbackServer(http.server.HTTPServer):
    def __init__(self, port: int, expected_state: str) -> None:
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.expected_state = expected_state
        self.result: dict[str, str] | None = None
        self.event = threading.Event()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, *args: Any) -> None:  # silence the default stderr log
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's contract
        url = urlparse(self.path)
        if url.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        ok = query.get("state") == self.server.expected_state and "code" in query
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(_SUCCESS_PAGE if ok else _FAILURE_PAGE)
        self.server.result = query
        self.server.event.set()


def authorize_url(profile: Profile, *, state: str, challenge: str, redirect_uri: str) -> str:
    query = {
        "response_type": "code",
        "client_id": profile.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(profile.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_domain(profile)}/oauth2/authorize?{urlencode(query)}"


def _domain(profile: Profile) -> str:
    if not profile.oidc_domain:
        raise AuthError(
            f"profile '{profile.name}' has no oidc_domain; run "
            f"`coho configure --profile {profile.name} --oidc-domain … --client-id …`"
        )
    domain = profile.oidc_domain.rstrip("/")
    return domain if domain.startswith("http") else f"https://{domain}"


def login(
    profile: Profile,
    *,
    open_browser: Callable[[str], object] | None = None,
    timeout: float = 300.0,
    port: int | None = None,
    on_url: Callable[[str], None] | None = None,
    http: httpx.Client | None = None,
) -> TokenSet:
    """Run the browser-based PKCE flow and return the tokens. Does not store them.

    Args:
        profile: needs ``oidc_domain``, ``client_id`` and ``scopes``.
        open_browser: how to open the URL; defaults to ``webbrowser.open``.
        timeout: seconds to wait for the callback.
        port: overrides ``profile.callback_port``; ``0`` picks a free one (only
            useful with a provider that allows any loopback port).
        on_url: called with the authorization URL, for printing it as a fallback.
    """
    if not profile.can_login:
        raise AuthError(
            f"profile '{profile.name}' is missing oidc_domain or client_id; "
            "configure them, or pass a token with `coho login --token`"
        )
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    listen_port = profile.callback_port if port is None else port
    try:
        server = _CallbackServer(listen_port, state)
    except OSError as exc:
        raise AuthError(f"cannot listen on 127.0.0.1:{listen_port}: {exc}") from exc
    actual_port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{actual_port}/callback"
    url = authorize_url(profile, state=state, challenge=challenge, redirect_uri=redirect_uri)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if on_url:
            on_url(url)
        (open_browser or webbrowser.open)(url)
        if not server.event.wait(timeout):
            raise AuthError(f"no sign-in completed within {int(timeout)} seconds")
    finally:
        server.shutdown()
        server.server_close()

    result = server.result or {}
    if "error" in result:
        raise AuthError(f"the identity provider refused: {result['error']}")
    if result.get("state") != state or "code" not in result:
        raise AuthError("callback did not carry the expected state and code")

    return exchange_code(
        profile, code=result["code"], verifier=verifier, redirect_uri=redirect_uri, http=http
    )


def exchange_code(
    profile: Profile,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    http: httpx.Client | None = None,
) -> TokenSet:
    body = _token_request(
        profile,
        {
            "grant_type": "authorization_code",
            "client_id": profile.client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        http=http,
    )
    return TokenSet.from_token_response(body)


def refresh(profile: Profile, tokens: TokenSet, http: httpx.Client | None = None) -> TokenSet:
    """A new access token from the refresh token. Cognito does not rotate the refresh token."""
    if not tokens.refresh_token:
        raise NotLoggedIn("no refresh token; run `coho login` again")
    body = _token_request(
        profile,
        {
            "grant_type": "refresh_token",
            "client_id": profile.client_id,
            "refresh_token": tokens.refresh_token,
        },
        http=http,
    )
    return TokenSet.from_token_response(body, refresh_token=tokens.refresh_token)


def _token_request(
    profile: Profile, form: dict[str, Any], *, http: httpx.Client | None
) -> dict[str, Any]:
    url = f"{_domain(profile)}/oauth2/token"
    client = http or httpx.Client(timeout=30.0)
    try:
        response = client.post(url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise AuthError(f"token endpoint unreachable: {exc}") from exc
    finally:
        if http is None:
            client.close()
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or "access_token" not in body:
        reason = (
            body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        )
        raise AuthError(f"token request refused: {reason}")
    return dict(body)


# ---------------------------------------------------------------------------
# The provider the transport calls
# ---------------------------------------------------------------------------


class TokenProvider:
    """Hands the transport a live access token, refreshing and re-storing as needed.

    Resolution order: ``COHO_ACCESS_TOKEN`` → explicit ``token`` → the profile's store.
    """

    def __init__(
        self,
        profile: Profile,
        store: TokenStore | None = None,
        *,
        token: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        self.store = store
        self._explicit = token
        self._http = http
        self._cached: TokenSet | None = None

    def current(self) -> TokenSet | None:
        """The stored tokens, without refreshing. ``None`` when not logged in."""
        if env := os.environ.get(ENV_ACCESS_TOKEN):
            return TokenSet(access_token=env)
        if self._explicit:
            return TokenSet(access_token=self._explicit)
        if self._cached is None and self.store is not None:
            self._cached = self.store.load(self.profile.name)
        return self._cached

    def __call__(self) -> str | None:
        tokens = self.current()
        if tokens is None:
            raise NotLoggedIn(f"not logged in to profile '{self.profile.name}'; run `coho login`")
        if tokens.needs_refresh() and tokens.refresh_token and self.profile.can_login:
            tokens = refresh(self.profile, tokens, http=self._http)
            self._cached = tokens
            if self.store is not None:
                self.store.save(self.profile.name, tokens)
        return tokens.access_token

    def forget(self) -> None:
        self._cached = None
        if self.store is not None:
            self.store.delete(self.profile.name)


__all__ = [
    "TokenSet",
    "TokenStore",
    "FileTokenStore",
    "KeyringTokenStore",
    "TokenProvider",
    "token_store_for",
    "login",
    "refresh",
    "authorize_url",
    "claims",
    "ENV_ACCESS_TOKEN",
]
