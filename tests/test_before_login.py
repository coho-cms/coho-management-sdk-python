"""What a person does before they have a login: sign up, look at an invitation, accept it.

None of these may need a stored login, and none may send one. The first property is
the bug these tests exist for: every request used to ask the token provider first,
which rightly refuses when there is nothing stored, so the very people these calls
are for got NOT_LOGGED_IN before anything was sent.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from coho_management_sdk import Coho
from coho_management_sdk.auth import FileTokenStore, TokenProvider
from coho_management_sdk.errors import NotLoggedIn
from coho_management_sdk.profiles import Profile
from coho_management_sdk.testing import INVITATION_TOKEN, FakeBff


@pytest.fixture
def logged_out(bff: FakeBff, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Coho:
    """A client with an empty token store: exactly a person who has never logged in."""
    monkeypatch.delenv("COHO_ACCESS_TOKEN", raising=False)
    profile = Profile(name="fresh", url=bff.url, token_store="file")
    provider = TokenProvider(profile, FileTokenStore(tmp_path / "credentials.json"))
    return Coho(bff.url, token_provider=provider)


def test_an_authenticated_call_still_refuses_locally(logged_out: Coho, bff: FakeBff) -> None:
    before = len(bff.requests)
    with pytest.raises(NotLoggedIn):
        logged_out.me()
    assert len(bff.requests) == before  # refused before anything was sent


def test_invitation_lookup_needs_no_login(logged_out: Coho, bff: FakeBff) -> None:
    inv = logged_out.invitation_lookup(INVITATION_TOKEN)
    assert inv.account_name == "Acme" and inv.status == "pending"
    assert "Authorization" not in bff.last().headers


def test_invitation_accept_needs_no_login(logged_out: Coho, bff: FakeBff) -> None:
    location = logged_out.invitation_accept_start(INVITATION_TOKEN, display_name="Jane")
    assert location.startswith("https://idp.example/oauth2/authorize")
    sent = bff.last()
    assert "Authorization" not in sent.headers
    assert sent.get_json() == {"invitation": INVITATION_TOKEN, "displayName": "Jane"}


def test_before_login_calls_never_send_a_login_even_when_there_is_one(
    coho: Coho, bff: FakeBff
) -> None:
    """A signed-in caller looking at an invitation for someone else must not present
    their own identity with it."""
    coho.invitation_lookup(INVITATION_TOKEN)
    assert "Authorization" not in bff.last().headers


def test_signup_is_a_page_to_open_and_makes_no_request(logged_out: Coho, bff: FakeBff) -> None:
    before = len(bff.requests)
    url = logged_out.signup_url("Maya & Co", display_name="Maya")
    assert len(bff.requests) == before

    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}" == bff.url
    assert parsed.path == "/auth/signup"
    assert parse_qs(parsed.query) == {
        "accountName": ["Maya & Co"],
        "displayName": ["Maya"],
        "returnTo": ["/api/v1/me"],
    }
    # Spaces as %20, never `+`, and the ampersand in the name stays inside its value.
    assert "Maya%20%26%20Co" in parsed.query and "+" not in parsed.query


def test_signup_needs_an_account_name(logged_out: Coho) -> None:
    for blank in ("", "   "):
        with pytest.raises(ValueError):
            logged_out.signup_url(blank)
