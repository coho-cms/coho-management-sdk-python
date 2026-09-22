from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from coho_management_sdk import auth
from coho_management_sdk.errors import AuthError, NotLoggedIn
from coho_management_sdk.profiles import Config, Profile


def test_config_round_trip(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "config.toml")
    p = cfg.profile("staging", create=True)
    p.url = "https://staging.example"
    p.oidc_domain = "https://x.auth.us-east-1.amazoncognito.com"
    p.client_id = "abc"
    p.context.account = "acct"
    p.remember_project("acct", "marketing", "proj-1")
    cfg.current_profile = "staging"
    cfg.save()

    again = Config.load(cfg.path)
    q = again.profile()
    assert q.name == "staging" and q.url == "https://staging.example" and q.can_login
    assert q.context.account == "acct"
    assert q.resolve_project("acct", "marketing") == "proj-1"
    assert q.resolve_project("acct", "proj-2") == "proj-2"
    assert q.project_name("acct", "proj-1") == "marketing"
    assert (
        q.forget_project("acct", "proj-1") and q.resolve_project("acct", "marketing") == "marketing"
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows files have ACLs, not POSIX mode bits")
def test_files_holding_credentials_are_owner_only(tmp_path: Path) -> None:
    """The config may name accounts and the credentials file holds tokens."""
    cfg = Config(path=tmp_path / "config.toml")
    cfg.profile("p", create=True).url = "https://example"
    cfg.save()
    assert oct(os.stat(cfg.path).st_mode)[-3:] == "600"

    store = auth.FileTokenStore(tmp_path / "creds.json")
    store.save("p", auth.TokenSet(access_token="a"))
    assert oct(os.stat(store.path).st_mode)[-3:] == "600"


def test_env_overrides_context(monkeypatch: pytest.MonkeyPatch) -> None:
    p = Profile(name="x")
    p.context.account = "saved"
    monkeypatch.setenv("COHO_ACCOUNT", "from-env")
    monkeypatch.setenv("COHO_REF", "qa")
    ctx = p.context.with_env()
    assert ctx.account == "from-env" and ctx.ref == "qa" and ctx.project is None


def test_missing_profile_raises_keyerror(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml")
    with pytest.raises(KeyError):
        cfg.profile("nope")


def test_file_token_store(tmp_path: Path) -> None:
    store = auth.FileTokenStore(tmp_path / "creds.json")
    assert store.load("p") is None
    store.save("p", auth.TokenSet(access_token="a", refresh_token="r", expires_at=1.0))
    loaded = store.load("p")
    assert loaded is not None and loaded.refresh_token == "r"
    store.delete("p")
    assert store.load("p") is None


def test_provider_prefers_env_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = auth.FileTokenStore(tmp_path / "creds.json")
    store.save("p", auth.TokenSet(access_token="stored"))
    provider = auth.TokenProvider(Profile(name="p"), store)
    assert provider() == "stored"
    monkeypatch.setenv("COHO_ACCESS_TOKEN", "from-env")
    assert provider() == "from-env"


def test_provider_not_logged_in(tmp_path: Path) -> None:
    provider = auth.TokenProvider(Profile(name="p"), auth.FileTokenStore(tmp_path / "c.json"))
    with pytest.raises(NotLoggedIn):
        provider()


def test_provider_refreshes_near_expiry(tmp_path: Path) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200, json={"access_token": "fresh", "expires_in": 3600, "token_type": "Bearer"}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    profile = Profile(name="p", oidc_domain="https://idp.example", client_id="cid")
    store = auth.FileTokenStore(tmp_path / "c.json")
    store.save(
        "p", auth.TokenSet(access_token="old", refresh_token="rt", expires_at=time.time() + 10)
    )
    provider = auth.TokenProvider(profile, store, http=http)
    assert provider() == "fresh"
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "rt"
    assert "client_secret" not in calls[0]  # public client
    stored = store.load("p")
    assert stored is not None and stored.access_token == "fresh" and stored.refresh_token == "rt"


def test_pkce_login_end_to_end(tmp_path: Path) -> None:
    """Drive the loopback callback ourselves, standing in for the browser."""
    import threading
    import urllib.request
    from urllib.parse import parse_qs, urlparse

    seen: dict[str, str] = {}

    def token_endpoint(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 60}
        )

    http = httpx.Client(transport=httpx.MockTransport(token_endpoint))
    profile = Profile(
        name="p", oidc_domain="idp.example", client_id="cid", scopes=["openid", "coho-auth/self"]
    )

    def fake_browser(url: str) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert urlparse(url).netloc == "idp.example" and q["code_challenge_method"] == "S256"
        assert q["scope"] == "openid coho-auth/self" and q["client_id"] == "cid"

        def hit() -> None:
            urllib.request.urlopen(f"{q['redirect_uri']}?code=the-code&state={q['state']}").read()

        threading.Thread(target=hit, daemon=True).start()

    tokens = auth.login(profile, open_browser=fake_browser, timeout=10, port=0, http=http)
    assert tokens.access_token == "at" and tokens.refresh_token == "rt"
    assert seen["grant_type"] == "authorization_code" and seen["code"] == "the-code"
    assert "code_verifier" in seen and "client_secret" not in seen


def test_login_requires_provider_config() -> None:
    with pytest.raises(AuthError):
        auth.login(Profile(name="p"), open_browser=lambda u: None, timeout=1)


def test_claims_is_unverified_decoding() -> None:
    import base64

    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": "me", "exp": 1}).encode()).rstrip(b"=").decode()
    )
    assert auth.claims(f"h.{payload}.s") == {"sub": "me", "exp": 1}
    assert auth.claims("garbage") == {}
