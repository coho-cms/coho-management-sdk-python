"""The object model: ``Coho`` → ``Account`` → ``Project`` → ``Ref``.

Each level owns the path segment it adds and hands its children a transport. The
CLI is a thin layer over these classes; anything the CLI can do is one call here.

    coho = Coho.from_profile("staging")
    site = coho.account("acme").project("0192…")
    dev = site.ref("dev")
    post = dev.entries.get("0192…")
    dev.entries.put(post)                      # If-Match from post.etag
"""

from __future__ import annotations

import builtins
import hashlib
import mimetypes
import os
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote, urlencode

import httpx

from . import models as m
from .auth import TokenProvider, token_store_for
from .errors import AssetTooLarge, CohoError, NotFound, PreconditionRequired, SnapshotNotReady
from .profiles import Config, Profile
from .transport import Response, Transport, segment

JSON = dict[str, Any]

API = "/api/v1"


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class Coho:
    """A signed-in connection to one BFF.

    Args:
        url: the BFF base URL.
        token: a bearer token to use as-is (CI). ``COHO_ACCESS_TOKEN`` still wins.
        token_provider: a callable returning the token; built from ``profile`` when omitted.
        profile: the profile whose token store and refresh settings apply.
        transport: a prebuilt `Transport` — tests inject one.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        token_provider: Callable[[], str | None] | None = None,
        profile: Profile | None = None,
        transport: Transport | None = None,
        timeout: float | None = 30.0,
        http: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        if transport is None:
            if not url:
                raise ValueError("Coho() needs a url (or a transport)")
            if token_provider is None:
                prof = profile or Profile(name="adhoc", url=url, token_store="file")
                store = token_store_for(prof) if profile is not None and token is None else None
                token_provider = TokenProvider(prof, store, token=token, http=http)
            transport = Transport(url, token_provider, timeout=timeout, client=http)
        self.transport = transport
        self.token_provider = token_provider
        self.users = UsersApi(self.transport)
        self._me: m.Me | None = None

    @classmethod
    def from_profile(
        cls, name: str | None = None, *, config: Config | None = None, token: str | None = None
    ) -> Coho:
        """Build from ``~/.config/coho/config.toml``, ``$COHO_PROFILE`` or the current profile."""
        config = config or Config.load()
        profile = config.profile(name)
        url = config.effective_url(profile)
        if not url:
            raise CohoError(
                f"profile '{profile.name}' has no url; run `coho configure --url …`", code="NO_URL"
            )
        return cls(url, token=token, profile=profile)

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> Coho:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- session ---------------------------------------------------------------

    def me(self, *, refresh: bool = False) -> m.Me:
        """Who is signed in and which accounts they belong to. Cached per client."""
        if self._me is None or refresh:
            self._me = m.Me.from_dict(self.transport.get(f"{API}/me").body)
        return self._me

    def accounts(self) -> list[m.Membership]:
        return self.me().accounts

    @property
    def uses_project_token(self) -> bool:
        """True when this client acts with a project token (doc 29), not as a person."""
        try:
            token = self.token_provider() if self.token_provider else None
        except CohoError:
            return False
        return bool(token) and str(token).startswith(PROJECT_TOKEN_PREFIX)

    def account(self, account: str) -> Account:
        """By id or by name. A name is resolved through ``/me``; an id is used as-is.

        With a project token there is no ``/me`` to ask — the BFF refuses it, since a
        token is not a person — so the account must be given by id.
        """
        if self.uses_project_token:
            if not _looks_like_id(account):
                raise CohoError(
                    f"a project token needs the account's id, not a name ('{account}'): "
                    "there is no /me to look a name up in"
                )
            return Account(self, account)
        membership = self.me().membership(account)
        if membership is None:
            # It may still be an id the session does not list (e.g. a stale cache).
            if _looks_like_id(account):
                return Account(self, account)
            raise NotFound(f"no account named or identified by '{account}' in this session")
        return Account(self, membership.account_id, membership=membership)

    def plans(self, *, include_retired: bool = False) -> list[m.Plan]:
        body = self.transport.get(
            f"{API}/plans", params={"includeRetired": include_retired or None}
        ).body
        return [m.Plan.from_dict(p) for p in (body or {}).get("plans", [])]

    # -- flows that finish in a browser -------------------------------------------
    #
    # What a person does before they have a login, so none of these sends one.

    def login_url(self, *, return_to: str = "/api/v1/me") -> str:
        """The BFF's sign-in page, for a person to open in a browser. Makes no request.

        Where an identity with no organization yet is asked to name one (UL-19): the
        CLI's own sign-in reaches the identity provider directly, so only this page
        can finish setting such an identity up. ``return_to`` is where the browser
        lands once it is done.
        """
        query = urlencode({"returnTo": return_to}, quote_via=quote)
        return f"{self.transport.base_url}/auth/login?{query}"

    def signup_url(
        self,
        account_name: str,
        *,
        display_name: str | None = None,
        return_to: str = "/api/v1/me",
    ) -> str:
        """The page that founds a new account, for a person to open in a browser.

        Makes no request. Sign-up is a browser flow on purpose: it must stay an
        interactive session, and before release that page is where a captcha is
        verified. ``return_to`` is where the browser lands once the account exists;
        the default shows the new user and account.
        """
        if not account_name.strip():
            raise ValueError("account_name is required")
        query = {"accountName": account_name.strip(), "returnTo": return_to}
        if display_name and display_name.strip():
            query["displayName"] = display_name.strip()
        # %20 rather than `+` for spaces: unambiguous whatever decodes the query.
        return f"{self.transport.base_url}/auth/signup?{urlencode(query, quote_via=quote)}"

    def invitation_lookup(self, token: str) -> m.Invitation:
        """What an invitation offers. Holding the token is the credential; no login needed."""
        resp = self.transport.post("/auth/invitations/lookup", {"invitation": token}, auth=False)
        return m.Invitation.from_dict(resp.body or {})

    def invitation_accept_start(self, token: str, *, display_name: str | None = None) -> str:
        """Start accepting an invitation. Returns the URL to open in a browser.

        A `POST` rather than a link, unlike sign-up: the token is a credential and
        travels in a body, never a URL (LG-16).
        """
        body = {"invitation": token, "displayName": display_name}
        resp = self.transport.post(
            "/auth/invitations/accept", {k: v for k, v in body.items() if v}, auth=False
        )
        return str(resp.body["location"])


# How a project token announces itself (doc 29).
PROJECT_TOKEN_PREFIX = "coho_pt_"


def _usage_query(
    grain: str | None,
    start: str | datetime | None,
    end: str | datetime | None,
    meters: builtins.list[str] | None,
) -> dict[str, str | None]:
    def instant(value: str | datetime | None) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "grain": grain,
        "from": instant(start),
        "to": instant(end),
        "meter": ",".join(meters) if meters else None,
    }


def _looks_like_id(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


# ---------------------------------------------------------------------------
# Users (self-service)
# ---------------------------------------------------------------------------


class UsersApi:
    """Acting on yourself: rename, list attached identities, detach one."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def rename(self, user_id: str, display_name: str) -> m.Me:
        resp = self._t.patch(f"{API}/users/{segment(user_id)}", {"displayName": display_name})
        return m.Me.from_dict(resp.body)

    def logins(self, user_id: str) -> list[m.Login]:
        body = self._t.get(f"{API}/users/{segment(user_id)}/logins").body
        return [m.Login.from_dict(x) for x in (body or {}).get("logins", [])]

    def detach_login(self, user_id: str, login_id: str) -> None:
        self._t.delete(f"{API}/users/{segment(user_id)}/logins/{segment(login_id)}")


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class Account:
    """One account the user belongs to. Membership and invitations need ``admin``."""

    def __init__(
        self, coho: Coho, account_id: str, *, membership: m.Membership | None = None
    ) -> None:
        self.coho = coho
        self.id = account_id
        self.membership = membership
        self._t = coho.transport
        self.path = f"{API}/accounts/{segment(account_id)}"
        self.members = MembersApi(self)
        self.invitations = InvitationsApi(self)
        self.projects = ProjectsApi(self)

    @property
    def name(self) -> str | None:
        return self.membership.account_name if self.membership else None

    @property
    def role(self) -> str | None:
        return self.membership.role if self.membership else None

    def rename(self, name: str) -> m.AccountInfo:
        return m.AccountInfo.from_dict(self._t.patch(self.path, {"name": name}).body)

    def entitlements(self) -> m.Entitlements:
        return m.Entitlements.from_dict(self._t.get(f"{self.path}/entitlements").body)

    def events(self, *, limit: int | None = None) -> list[m.AuditEvent]:
        body = self._t.get(f"{self.path}/events", params={"limit": limit}).body
        return [m.AuditEvent.from_dict(e) for e in (body or {}).get("events", [])]

    def project(self, project_id: str) -> Project:
        return Project(self, project_id)

    def usage(
        self,
        *,
        grain: str | None = None,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        meters: builtins.list[str] | None = None,
    ) -> m.AccountUsageReport:
        """Every project's usage summed, and each project's totals (admin).

        A member is refused ``403 FORBIDDEN``, and a project token ``TOKEN_NOT_ALLOWED``,
        since the report names projects they may hold no role on. Arguments as
        `Project.usage`.
        """
        body = self._t.get(
            f"{self.path}/projects/usage", params=_usage_query(grain, start, end, meters)
        ).body
        return m.AccountUsageReport.from_dict(body)


class MembersApi:
    def __init__(self, account: Account) -> None:
        self._a = account
        self._t = account._t

    def list(self) -> list[m.Member]:
        body = self._t.get(f"{self._a.path}/users").body
        return [m.Member.from_dict(u) for u in (body or {}).get("users", [])]

    def change_role(self, user_id: str, role: str) -> m.Member:
        resp = self._t.patch(f"{self._a.path}/members/{segment(user_id)}", {"role": role})
        return m.Member.from_dict(resp.body)

    def remove(self, user_id: str) -> None:
        """Remove a member — or leave, when ``user_id`` is your own."""
        self._t.delete(f"{self._a.path}/members/{segment(user_id)}")


class InvitationsApi:
    def __init__(self, account: Account) -> None:
        self._a = account
        self._t = account._t

    def list(self) -> list[m.Invitation]:
        body = self._t.get(f"{self._a.path}/invitations").body
        return [m.Invitation.from_dict(i) for i in (body or {}).get("invitations", [])]

    def create(self, email: str, *, role: str = "member") -> m.IssuedInvitation:
        """⚠️ The token in the result is shown once. Deliver it over a channel that does not leak."""
        resp = self._t.post(f"{self._a.path}/invitations", {"email": email, "role": role})
        return m.IssuedInvitation.from_dict(resp.body)

    def revoke(self, invitation_id: str) -> None:
        self._t.delete(f"{self._a.path}/invitations/{segment(invitation_id)}")


class ProjectsApi:
    """List, create and open projects."""

    def __init__(self, account: Account) -> None:
        self._a = account
        self._t = account._t

    def list(self) -> list[m.ProjectSummary]:
        """The projects in this account you can open, and your role on each.

        An account admin sees every project; anyone else the ones granted to them.
        """
        body = self._t.get(f"{self._a.path}/projects").body
        return [m.ProjectSummary.from_dict(p) for p in (body or {}).get("projects", [])]

    def create(self, name: str) -> Project:
        """Create and bootstrap a project: trunk ``v0.0.x`` and four environments."""
        resp = self._t.post(f"{self._a.path}/projects", {"name": name})
        info = m.ProjectInfo.from_dict(resp.body)
        return Project(self._a, info.id, info=info)

    def get(self, project_id: str) -> Project:
        project = Project(self._a, project_id)
        project.info()
        return project


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project:
    def __init__(
        self, account: Account, project_id: str, *, info: m.ProjectInfo | None = None
    ) -> None:
        self.account = account
        self.id = project_id
        self._t = account._t
        self._info = info
        self.path = f"{account.path}/projects/{segment(project_id)}"
        self.refs = RefsApi(self)
        self.branches = BranchesApi(self)
        self.tags = TagsApi(self)
        self.environments = EnvironmentsApi(self)
        self.tiers = TiersApi(self)
        self.roles = RolesApi(self)
        self.delivery_keys = DeliveryKeysApi(self)
        self.tokens = ProjectTokensApi(self)

    def info(self, *, refresh: bool = False) -> m.ProjectInfo:
        if self._info is None or refresh:
            self._info = m.ProjectInfo.from_dict(self._t.get(self.path).body)
        return self._info

    @property
    def name(self) -> str:
        return self.info().name

    def rename(self, name: str) -> str:
        """Rename it, returning the name as stored (trimmed). An account admin's.

        Nothing else about the project changes: its id, its delivery paths and its keys
        are untouched, so held references stay valid. An archived project can still be
        renamed. Raises on a blank name, and on one another project in the account holds
        (``PROJECT_NAME_TAKEN``, ignoring case).

        ⚠️ A name the CLI remembered in a profile is now stale — the registry
        (`Profile.remember_project`) is keyed by name, and this does not reach it.
        """
        stored = str((self._t.patch(self.path, {"name": name}).body or {}).get("name", ""))
        # Re-read on the next `info()`: the cached document still has the old name.
        self._info = None
        return stored

    def ref(self, name: str, *, at: int | None = None) -> Ref:
        """A branch, tag or environment; with ``at``, read as it was at that seq."""
        return Ref(self, name, at=at)

    # -- diff and merge --------------------------------------------------------

    def diff(self, from_ref: str, to_ref: str, *, mode: str = "diverged") -> m.Diff:
        """Compare ``to`` against ``from``. ``diverged`` (three-dot) also previews the merge."""
        body = self._t.get(
            f"{self.path}/diff", params={"from": from_ref, "to": to_ref, "mode": mode}
        ).body
        return m.Diff.from_dict(body)

    def merge(
        self,
        source: str,
        target: str,
        *,
        message: str | None = None,
        resolutions: builtins.list[m.Resolution] | builtins.list[JSON] | None = None,
        expected_token: str | None = None,
    ) -> m.MergeResult:
        """Three-way merge ``source`` into ``target``. Raises `MergeConflict` with the list."""
        body: JSON = {"source": source, "target": target}
        if message:
            body["message"] = message
        if resolutions:
            body["resolutions"] = [
                r.to_dict() if isinstance(r, m.Resolution) else r for r in resolutions
            ]
        if expected_token:
            body["expectedToken"] = expected_token
        return m.MergeResult.from_dict(self._t.post(f"{self.path}/merges", body).body)

    # -- export ----------------------------------------------------------------

    def export(
        self,
        ref: str,
        *,
        format: str = "ndjson",
        to: str | Path | IO[bytes] | None = None,
        stream: bool | None = None,
    ) -> m.Export:
        """Export a tag or branch.

        A tag is served from its prebuilt image, usually via a ``302`` to a presigned
        URL, which is followed. A branch is computed at its current tip; the sequence
        it was resolved at comes back as ``seq``. Bytes go to ``to`` (a path or a
        binary file object); with ``to=None`` they go to ``./<ref>.<format>``.
        """
        resp = self._t.get(
            f"{self.path}/refs/{segment(ref)}/export",
            params={"format": format, "stream": stream},
            follow_redirects=True,
            stream=True,
            headers={"Accept": "*/*"},
        )
        redirected = str(resp.raw.url) if resp.raw.history else None
        seq_header = resp.headers.get("x-coho-export-seq")
        seq = int(seq_header) if seq_header and seq_header.isdigit() else None
        safe_name = ref.replace("/", "_")
        written = 0
        chunks: list[bytes] = []
        out_path: str | None = None
        sink: IO[bytes] | None = None
        close = False
        if to is None or isinstance(to, str | Path):
            out_path = str(to) if to is not None else f"{safe_name}.{format}"
            sink = open(out_path, "wb")  # noqa: SIM115 - closed in finally
            close = True
        else:
            sink = to
        try:
            for chunk in self._t.stream_bytes(resp):
                sink.write(chunk)
                written += len(chunk)
                if format == "tar" and len(chunks) < 64:
                    chunks.append(chunk)
        finally:
            if close and sink is not None:
                sink.close()
        manifest: JSON | None = None
        if "json" in resp.headers.get("content-type", "") and chunks:
            try:
                import json  # noqa: PLC0415

                manifest = json.loads(b"".join(chunks))
            except ValueError:
                manifest = None
        return m.Export(
            ref=ref,
            format=format,
            path=out_path,
            seq=seq,
            bytes_written=written,
            redirected_to=redirected,
            manifest=manifest,
        )

    # -- preview ---------------------------------------------------------------

    def preview(self, ref: str) -> Preview:
        return Preview(self, ref)

    # -- usage -----------------------------------------------------------------

    def usage(
        self,
        *,
        grain: str | None = None,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        meters: builtins.list[str] | None = None,
    ) -> m.UsageReport:
        """The project's usage (coho-data doc 41): anyone who can read the project, tokens too.

        ``grain`` is ``hour`` (the default: the last day) or ``day`` (the last 30 days).
        ``start`` is rounded down and ``end`` up to whole periods; hourly rows are kept 90
        days. ``meters`` narrows the report to those names. A range the stored data cannot
        answer is refused ``400 USAGE_QUERY_INVALID``.
        """
        body = self._t.get(
            f"{self.path}/usage", params=_usage_query(grain, start, end, meters)
        ).body
        return m.UsageReport.from_dict(body)


class RefsApi:
    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self, *, kind: str | None = None) -> list[m.RefInfo]:
        body = self._t.get(f"{self._p.path}/refs").body
        refs = [m.RefInfo.from_dict(r) for r in (body or {}).get("refs", [])]
        return [r for r in refs if kind is None or r.kind == kind]

    def get(self, name: str) -> m.RefInfo:
        return m.RefInfo.from_dict(self._t.get(f"{self._p.path}/refs/{segment(name)}").body)

    def describe(self, name: str, description: str | None) -> m.RefInfo:
        """Set (or clear, with ``None``/blank) a ref's standing description."""
        body = {"description": description or ""}
        return m.RefInfo.from_dict(self._t.patch(f"{self._p.path}/refs/{segment(name)}", body).body)


class BranchesApi:
    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> list[m.Branch]:
        body = self._t.get(f"{self._p.path}/branches").body
        return [m.Branch.from_dict(b) for b in (body or {}).get("branches", [])]

    def create(
        self,
        name: str,
        *,
        from_ref: str,
        kind: str = "feature_branch",
        description: str | None = None,
        at: int | None = None,
    ) -> m.Branch:
        """O(1), copies nothing. ``kind='version_branch'`` needs ``branch:create_version``."""
        body: JSON = {"name": name, "from": from_ref, "kind": kind}
        if description:
            body["description"] = description
        if at is not None:
            body["at"] = at
        return m.Branch.from_dict(self._t.post(f"{self._p.path}/branches", body).body)


class TagsApi:
    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> list[m.RefInfo]:
        """Tags are refs of kind ``tag``; there is no separate listing."""
        return self._p.refs.list(kind="tag")

    def create(
        self,
        name: str,
        *,
        branch: str,
        description: str | None = None,
        at: int | None = None,
        validate: bool = False,
    ) -> m.Tag:
        """Cut an immutable tag on a **version** branch. Does not merge.

        Does not validate either, unless ``validate=True``: then every entry at the tag
        point is checked first, and if any fails, `TagContentInvalid` names them and
        nothing is tagged. That costs the server a read of the whole branch.
        """
        body: JSON = {"name": name, "ref": branch}
        if description:
            body["description"] = description
        if at is not None:
            body["at"] = at
        if validate:
            body["validate"] = True
        return m.Tag.from_dict(self._t.post(f"{self._p.path}/tags", body).body)


class EnvironmentsApi:
    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> list[m.Environment]:
        body = self._t.get(f"{self._p.path}/environments").body
        return [m.Environment.from_dict(e) for e in (body or {}).get("environments", [])]

    def get(self, name: str) -> m.Environment:
        for env in self.list():
            if env.name == name:
                return env
        raise NotFound(f"no environment named '{name}'", code="NOT_FOUND", status=404)

    def create(self, name: str, *, tier: str, target: str | None = None) -> m.Environment:
        body: JSON = {"name": name, "tier": tier}
        if target:
            body["target"] = target
        return m.Environment.from_dict(self._t.post(f"{self._p.path}/environments", body).body)

    def promote(
        self,
        name: str,
        target: str | None,
        *,
        expected_target: str | None = None,
        reason: str | None = None,
        wait: bool = True,
        timeout: float = 300.0,
        poll: float = 2.0,
        on_wait: Callable[[SnapshotNotReady, float], None] | None = None,
    ) -> m.Environment:
        """Repoint an environment: promote, roll back, or unset with ``target=None``.

        A snapshot environment pointed at a tag whose image is still building answers
        ``SNAPSHOT_NOT_READY``; with ``wait=True`` this retries until it is ready or
        ``timeout`` passes. ``expected_target`` makes it conditional (``ENVIRONMENT_MOVED``
        otherwise), which is what a rollback should always carry.
        """
        body: JSON = {"target": target}
        if expected_target is not None:
            body["expectedTarget"] = expected_target
        if reason:
            body["reason"] = reason
        deadline = time.monotonic() + timeout
        delay = poll
        while True:
            try:
                resp = self._t.put(f"{self._p.path}/environments/{segment(name)}", body)
                return m.Environment.from_dict(resp.body)
            except SnapshotNotReady as exc:
                remaining = deadline - time.monotonic()
                if not wait or remaining <= 0:
                    raise
                if on_wait:
                    on_wait(exc, remaining)
                time.sleep(min(delay, remaining))
                delay = min(delay * 1.5, 15.0)

    def unset(
        self, name: str, *, reason: str | None = None, expected_target: str | None = None
    ) -> m.Environment:
        return self.promote(name, None, reason=reason, expected_target=expected_target, wait=False)

    def delete(self, name: str) -> None:
        self._t.delete(f"{self._p.path}/environments/{segment(name)}")

    def history(
        self, name: str, *, limit: int | None = None, offset: int | None = None
    ) -> m.RefHistory:
        body = self._t.get(
            f"{self._p.path}/environments/{segment(name)}/history",
            params={"limit": limit, "offset": offset},
        ).body
        return m.RefHistory.from_dict(body)

    def rollback(
        self, name: str, *, reason: str | None = None, wait: bool = True, timeout: float = 300.0
    ) -> m.Environment:
        """Repoint to the previous target from history, conditional on the current one.

        There is no rollback endpoint by design (M6-9): this reads the history, takes
        the entry before the current one, and promotes with ``expected_target`` set to
        what the environment points at now — so if someone already fixed it forward,
        the rollback fails instead of undoing their work.
        """
        hist = self.history(name, limit=10)
        entries = [h for h in hist.history if h.to_target is not None]
        if len(entries) < 2:
            raise CohoError(
                f"'{name}' has no previous target to roll back to", code="NO_PREVIOUS_TARGET"
            )
        current, previous = entries[0].to_target, entries[1].to_target
        return self.promote(
            name,
            previous,
            expected_target=current,
            reason=reason or f"rollback from {current}",
            wait=wait,
            timeout=timeout,
        )


class TiersApi:
    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> list[m.Tier]:
        body = self._t.get(f"{self._p.path}/tiers").body
        return [m.Tier.from_dict(t) for t in (body or {}).get("tiers", [])]

    def create(self, tier_id: str, *, resolves: str, ord: int) -> m.Tier:
        """``owner`` only. ``resolves`` (``live``/``snapshot``) is immutable afterwards.

        ``ord`` is the position to insert at: a taken one moves that tier and the ones
        after it along by one, and one past the end is the end. The tier comes back at
        the position it got.
        """
        body = {"id": tier_id, "resolves": resolves, "ord": ord}
        return m.Tier.from_dict(self._t.post(f"{self._p.path}/tiers", body).body)

    def move(self, tier_id: str, ord: int) -> m.Tier:
        """``owner`` only. Moves the tier to ``ord``; the tiers it passes shift by one.

        One past the end is the end. Positions always run 0 to n-1.
        """
        resp = self._t.patch(f"{self._p.path}/tiers/{segment(tier_id)}", {"ord": ord})
        return m.Tier.from_dict(resp.body)

    def delete(self, tier_id: str) -> None:
        self._t.delete(f"{self._p.path}/tiers/{segment(tier_id)}")


class RolesApi:
    """Project roles: who holds ``viewer``…``owner``. Needs ``role:grant`` (owner)."""

    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> list[m.ProjectRole]:
        body = self._t.get(f"{self._p.path}/roles").body
        return [m.ProjectRole.from_dict(r) for r in (body or {}).get("roles", [])]

    def grant(self, actor_id: str, role: str) -> m.ProjectRole:
        """Idempotent: repeating it changes the role rather than adding a second grant."""
        resp = self._t.put(f"{self._p.path}/roles/{segment(actor_id)}", {"role": role})
        return m.ProjectRole.from_dict(resp.body)

    def revoke(self, actor_id: str) -> None:
        self._t.delete(f"{self._p.path}/roles/{segment(actor_id)}")


class ProjectTokensApi:
    """API keys for automation, one project and one role each (doc 29). Needs ``owner``.

    Use one as ``COHO_TOKEN`` (or ``Coho(url, token=…)``): it is sent as the bearer and
    works only for this project's content.
    """

    ROLES = ("viewer", "author", "maintainer", "release_manager")

    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> builtins.list[m.ProjectToken]:
        """Every token, revoked and expired ones included."""
        body = self._t.get(f"{self._p.path}/tokens").body
        return [m.ProjectToken.from_dict(t) for t in (body or {}).get("tokens", [])]

    def create(
        self, label: str, role: str, *, expires_in_days: int | None = None
    ) -> m.CreatedProjectToken:
        """⚠️ The token is in the result and nowhere else, ever. ``role`` is below owner."""
        body: JSON = {"label": label, "role": role}
        if expires_in_days is not None:
            body["expiresInDays"] = expires_in_days
        return m.CreatedProjectToken.from_dict(self._t.post(f"{self._p.path}/tokens", body).body)

    def revoke(self, token_id: str) -> None:
        """Takes effect on the token's next request. Revoking twice is not an error."""
        self._t.delete(f"{self._p.path}/tokens/{segment(token_id)}")


class DeliveryKeysApi:
    """Keys the delivery API accepts. Needs ``delivery:keys`` (owner)."""

    def __init__(self, project: Project) -> None:
        self._p = project
        self._t = project._t

    def list(self) -> m.DeliveryKeyList:
        return m.DeliveryKeyList.from_dict(self._t.get(f"{self._p.path}/delivery-keys").body)

    def issue(
        self,
        *,
        label: str | None = None,
        refs: builtins.list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> m.IssuedDeliveryKey:
        """⚠️ The key is in the result and nowhere else, ever. ``refs=None`` grants every ref."""
        body: JSON = {}
        if label:
            body["label"] = label
        if refs is not None:
            body["refs"] = refs
        if expires_in_days is not None:
            body["expiresInDays"] = expires_in_days
        return m.IssuedDeliveryKey.from_dict(
            self._t.post(f"{self._p.path}/delivery-keys", body).body
        )

    def revoke(self, key_id: str) -> None:
        self._t.delete(f"{self._p.path}/delivery-keys/{segment(key_id)}")

    def set_public(self, refs: builtins.list[str]) -> m.DeliveryKeyList:
        """Replace the set of environments readable without a key. Environments only."""
        return m.DeliveryKeyList.from_dict(
            self._t.put(f"{self._p.path}/delivery-keys/public", {"refs": refs}).body
        )


class Preview:
    """The delivery contract, served live from authoring for a ref — through the BFF."""

    def __init__(self, project: Project, ref: str) -> None:
        self._p = project
        self._t = project._t
        self.ref = ref
        self.path = f"{project.path}/preview/{segment(ref)}"

    def entries(
        self,
        *,
        type: str | None = None,
        locale: str | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> JSON:
        params = {"type": type, "locale": locale, "order": order, "limit": limit, "offset": offset}
        return dict(self._t.get(f"{self.path}/entries", params=params).body or {})

    def entry(self, entry_id: str, *, locale: str | None = None) -> JSON:
        return dict(
            self._t.get(f"{self.path}/entries/{segment(entry_id)}", params={"locale": locale}).body
            or {}
        )


# ---------------------------------------------------------------------------
# Ref: types and entries
# ---------------------------------------------------------------------------


class Ref:
    """A branch, tag or environment, as a place to read and (for a branch) write content.

    With ``at``, every read is of the ref as it was at that seq (coho-data doc 59): types,
    traits, entries, their history and references, and assets, all of the same moment. A
    seq is what an entry's ``history`` reports for each write. On a tag ``at`` may go back
    but not past the tag. Such a ref writes nothing: the server refuses a write from it
    with `AtOutOfRange`, as it refuses a seq the project has not reached.
    """

    def __init__(self, project: Project, name: str, *, at: int | None = None) -> None:
        self.project = project
        self.name = name
        self.at = at
        self._t = project._t if at is None else project._t.with_params(at=at)
        self.path = f"{project.path}/refs/{segment(name)}"
        self.types = TypesApi(self)
        self.traits = TraitsApi(self)
        self.entries = EntriesApi(self)
        self.assets = AssetsApi(self)

    def info(self) -> m.RefInfo:
        return self.project.refs.get(self.name)

    def export(self, **kw: Any) -> m.Export:
        self._now_only("export")
        return self.project.export(self.name, **kw)

    def preview(self) -> Preview:
        self._now_only("preview")
        return self.project.preview(self.name)

    def _now_only(self, what: str) -> None:
        # Neither takes `at` yet, so either would answer with the ref as it is now.
        if self.at is not None:
            raise ValueError(f"{what} reads the ref as it is now; it does not take at={self.at}")


class TypesApi:
    def __init__(self, ref: Ref) -> None:
        self._r = ref
        self._t = ref._t

    def list(self, *, effective: bool = False) -> list[m.ContentType]:
        """Every type on the ref. ``effective`` folds in what each inherits and its traits give."""
        params = {"resolve": "effective"} if effective else None
        body = self._t.get(f"{self._r.path}/types", params=params).body
        return [m.ContentType.from_dict(t) for t in (body or {}).get("types", [])]

    def get(self, slug: str, *, effective: bool = False) -> m.ContentType:
        """One type as declared, what a ``put`` sends back, or ``effective``: every field it has."""
        params = {"resolve": "effective"} if effective else None
        resp = self._t.get(f"{self._r.path}/types/{segment(slug)}", params=params)
        return m.ContentType.from_dict(resp.body, etag=resp.etag)

    def put(
        self,
        slug: str,
        definition: JSON,
        *,
        if_match: str | None = None,
        confirm_destructive: bool = False,
        force: bool = False,
    ) -> m.ContentType:
        """Create or update a content type.

        Creating needs no ``If-Match``. Updating does: pass the ETag from the last
        ``get`` as ``if_match``, or ``force=True`` to read the current ETag and
        overwrite whatever is there. A destructive change (removing a field, dropping a
        locale) is refused unless ``confirm_destructive`` is set.
        """
        body: JSON = {"definition": definition}
        if confirm_destructive:
            body["confirmDestructive"] = True
        etag = if_match
        if etag is None and force:
            etag = self._current_etag(slug)
        resp = self._t.put(f"{self._r.path}/types/{segment(slug)}", body, if_match=etag)
        return m.ContentType.from_dict(resp.body, etag=resp.etag)

    def delete(self, slug: str, *, confirm_destructive: bool = False) -> None:
        """Tombstone a type. Refused while entries resolve to it unless ``confirm_destructive``."""
        self._t.delete(
            f"{self._r.path}/types/{segment(slug)}",
            params={"confirmDestructive": confirm_destructive or None},
        )

    def _current_etag(self, slug: str) -> str | None:
        try:
            return self.get(slug).etag
        except NotFound:
            return None


class TraitsApi:
    """Traits on a ref (doc 05 §10): named groups of fields that types take, by ``traits``."""

    def __init__(self, ref: Ref) -> None:
        self._r = ref
        self._t = ref._t

    def list(self) -> list[m.Trait]:
        body = self._t.get(f"{self._r.path}/traits").body
        return [m.Trait.from_dict(t) for t in (body or {}).get("traits", [])]

    def get(self, slug: str) -> m.Trait:
        resp = self._t.get(f"{self._r.path}/traits/{segment(slug)}")
        return m.Trait.from_dict(resp.body, etag=resp.etag)

    def put(
        self,
        slug: str,
        definition: JSON,
        *,
        if_match: str | None = None,
        confirm_destructive: bool = False,
        force: bool = False,
    ) -> m.Trait:
        """Create or update a trait: ``{"_name": …, "fields": […]}`` and nothing else.

        A change reaches every type that takes the trait and every type below them. A
        destructive one is refused as ``DestructiveSchemaChange``, its context naming
        ``affectedTypes`` and ``entryCount``, unless ``confirm_destructive`` is set.
        Updating needs ``if_match``, or ``force=True`` to overwrite what is there.
        """
        body: JSON = {"definition": definition}
        if confirm_destructive:
            body["confirmDestructive"] = True
        etag = if_match
        if etag is None and force:
            etag = self._current_etag(slug)
        resp = self._t.put(f"{self._r.path}/traits/{segment(slug)}", body, if_match=etag)
        return m.Trait.from_dict(resp.body, etag=resp.etag)

    def delete(self, slug: str, *, if_match: str | None = None) -> None:
        """Tombstone a trait. Refused as ``TraitInUse`` while a type takes it, always."""
        etag = if_match or self._current_etag(slug)
        self._t.delete(f"{self._r.path}/traits/{segment(slug)}", if_match=etag)

    def _current_etag(self, slug: str) -> str | None:
        try:
            return self.get(slug).etag
        except NotFound:
            return None


class EntriesApi:
    def __init__(self, ref: Ref) -> None:
        self._r = ref
        self._t = ref._t

    def list(
        self, *, type: str | None = None, limit: int | None = None, offset: int | None = None
    ) -> m.EntryPage:
        """One page. ``limit`` is 1..1000 (default 100); out of range is refused, not clamped."""
        body = self._t.get(
            f"{self._r.path}/entries", params={"type": type, "limit": limit, "offset": offset}
        ).body
        return m.EntryPage.from_dict(body)

    def iterate(self, *, type: str | None = None, page_size: int = 100) -> Iterator[m.Entry]:
        """Every entry, page by page."""
        offset = 0
        while True:
            page = self.list(type=type, limit=page_size, offset=offset)
            yield from page.entries
            if not page.page.has_more or not page.entries:
                return
            offset += len(page.entries)

    def get(self, entry_id: str) -> m.Entry:
        resp = self._t.get(f"{self._r.path}/entries/{segment(entry_id)}")
        return m.Entry.from_dict(resp.body, etag=resp.etag)

    def create(self, *, type: str, slug: str, fields: JSON) -> m.Entry:
        body = {"type": type, "slug": slug, "fields": fields}
        resp = self._t.post(f"{self._r.path}/entries", body)
        return m.Entry.from_dict(resp.body, etag=resp.etag)

    def put(
        self,
        entry: m.Entry | str,
        fields: JSON | None = None,
        *,
        if_match: str | None = None,
        force: bool = False,
    ) -> m.Entry:
        """Update an entry's fields.

        Pass the `Entry` from a ``get`` (its ``etag`` becomes ``If-Match``), or an id
        with ``fields`` and ``if_match``. ``force=True`` reads the current ETag first and
        overwrites. Without any of those this raises `PreconditionRequired` locally
        rather than sending a request the server will refuse with ``428``.
        """
        if isinstance(entry, m.Entry):
            entry_id = entry.id
            fields = entry.fields if fields is None else fields
            etag = if_match or entry.etag
        else:
            entry_id = entry
            etag = if_match
        if fields is None:
            raise ValueError("put() needs fields")
        if etag is None:
            if not force:
                raise PreconditionRequired(
                    "an update needs the ETag from the read it is based on; pass if_match=…, "
                    "or force=True to overwrite the current version",
                    code="PRECONDITION_REQUIRED",
                    status=428,
                )
            etag = self.get(entry_id).etag
        resp = self._t.put(
            f"{self._r.path}/entries/{segment(entry_id)}", {"fields": fields}, if_match=etag
        )
        return m.Entry.from_dict(resp.body, etag=resp.etag)

    def delete(
        self, entry: m.Entry | str, *, if_match: str | None = None, force: bool = False
    ) -> str | None:
        """Tombstone an entry. Returns the tombstone's ETag. Same precondition rules as ``put``."""
        entry_id = entry.id if isinstance(entry, m.Entry) else entry
        etag = if_match or (entry.etag if isinstance(entry, m.Entry) else None)
        if etag is None:
            if not force:
                raise PreconditionRequired(
                    "a delete needs the ETag from the read it is based on; pass if_match=…, "
                    "or force=True to delete the current version",
                    code="PRECONDITION_REQUIRED",
                    status=428,
                )
            etag = self.get(entry_id).etag
        resp: Response = self._t.delete(
            f"{self._r.path}/entries/{segment(entry_id)}", if_match=etag
        )
        return resp.etag

    def history(
        self, entry_id: str, *, limit: int | None = None, offset: int | None = None
    ) -> m.History:
        body = self._t.get(
            f"{self._r.path}/entries/{segment(entry_id)}/history",
            params={"limit": limit, "offset": offset},
        ).body
        return m.History.from_dict(body)

    def references(self, entry_id: str) -> m.References:
        """Incoming links on this ref. Reports, never refuses."""
        return m.References.from_dict(
            self._t.get(f"{self._r.path}/entries/{segment(entry_id)}/references").body
        )


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

AssetSource = str | os.PathLike[str] | bytes
"""A path to a file, or the bytes themselves."""

_CHUNK = 1024 * 1024


class AssetsApi:
    """Files on a ref (doc 40): images, PDF, audio and video.

    An asset is a node like an entry, so it branches and merges; its bytes, addressed by
    their sha256, do not branch at all. Uploading is three requests, and ``upload`` does
    all of them:

    1. ``begin_upload``: the hash and size, which are signed into a presigned PUT;
    2. ``put_bytes``: the bytes, straight to object storage, never through the BFF;
    3. ``complete_upload``: the server checks what arrived and writes the asset.

    ⚠️ Every asset is public to anyone holding its URL, drafts included, and deleting an
    asset does not stop its URL working (doc 40 §7.0).
    """

    MAX_BYTES = 1 << 30
    """The per-file limit the server enforces, so a caller can refuse early."""

    def __init__(self, ref: Ref) -> None:
        self._r = ref
        self._t = ref._t

    def list(self, *, limit: int | None = None, offset: int | None = None) -> m.AssetPage:
        """One page, in slug order. ``limit`` is 1..1000 (default 100)."""
        body = self._t.get(f"{self._r.path}/assets", params={"limit": limit, "offset": offset}).body
        return m.AssetPage.from_dict(body or {})

    def iterate(self, *, page_size: int = 100) -> Iterator[m.Asset]:
        """Every asset, page by page."""
        offset = 0
        while True:
            page = self.list(limit=page_size, offset=offset)
            yield from page.assets
            if not page.page.has_more or not page.assets:
                return
            offset += len(page.assets)

    def get(self, asset_id: str) -> m.Asset:
        resp = self._t.get(f"{self._r.path}/assets/{segment(asset_id)}")
        return m.Asset.from_dict(resp.body, etag=resp.etag)

    def upload(
        self,
        source: AssetSource,
        *,
        slug: str,
        filename: str | None = None,
        title: str | None = None,
        alt: str | None = None,
        focal_point: m.FocalPoint | None = None,
        tags: builtins.list[str] | None = None,
        content_type: str | None = None,
    ) -> m.Asset:
        """Upload a file and write it as an asset on this branch.

        Hashes the file, starts the upload, PUTs the bytes (skipped when the project
        already holds them) and completes it. ``filename`` defaults to the path's name,
        and is required for bytes. Raises `AssetTooLarge` before sending anything when
        the file is over `MAX_BYTES`.
        """
        sha256, size = sha256_of(source)
        if filename is None:
            if isinstance(source, bytes):
                raise ValueError("upload() of bytes needs a filename")
            filename = Path(source).name
        if size > self.MAX_BYTES:
            raise AssetTooLarge(
                f"{filename} is {size} bytes; an asset may be at most {self.MAX_BYTES}",
                code="ASSET_TOO_LARGE",
                context={"limit": self.MAX_BYTES, "current": size},
            )
        guessed = content_type or mimetypes.guess_type(filename)[0]
        upload = self.begin_upload(
            sha256=sha256, byte_size=size, filename=filename, content_type=guessed
        )
        if upload.put is not None:
            self.put_bytes(upload, source, length=size)
        return self.complete_upload(
            upload.id, slug=slug, title=title, alt=alt, focal_point=focal_point, tags=tags
        )

    def begin_upload(
        self, *, sha256: str, byte_size: int, filename: str, content_type: str | None = None
    ) -> m.AssetUpload:
        """Start an upload. The hash and size must be exact: the store refuses anything else."""
        body: JSON = {"sha256": sha256, "byteSize": byte_size, "filename": filename}
        if content_type:
            body["contentType"] = content_type
        return m.AssetUpload.from_dict(self._t.post(f"{self._r.path}/assets/uploads", body).body)

    def put_bytes(self, upload: m.AssetUpload, source: AssetSource, *, length: int) -> None:
        """Send an upload's bytes to its presigned URL, streaming a file rather than reading it."""
        if upload.put is None:
            return
        content: bytes | Iterator[bytes] = source if isinstance(source, bytes) else _chunks(source)
        self._t.put_presigned(upload.put.url, upload.put.headers, content, length=length)

    def complete_upload(
        self,
        upload_id: str,
        *,
        slug: str,
        title: str | None = None,
        alt: str | None = None,
        focal_point: m.FocalPoint | None = None,
        tags: builtins.list[str] | None = None,
    ) -> m.Asset:
        """Finish an upload and write the asset. Completing again returns the same asset."""
        body = _asset_details(title=title, alt=alt, focal_point=focal_point, tags=tags)
        body["slug"] = slug
        resp = self._t.post(f"{self._r.path}/assets/uploads/{segment(upload_id)}/complete", body)
        return m.Asset.from_dict(resp.body, etag=resp.etag)

    def put(
        self,
        asset: m.Asset | str,
        *,
        title: str | None = None,
        alt: str | None = None,
        focal_point: m.FocalPoint | None = None,
        tags: builtins.list[str] | None = None,
        if_match: str | None = None,
        force: bool = False,
    ) -> m.Asset:
        """Replace an asset's metadata. The file itself is never editable.

        A whole replacement, as for entries. Given an `Asset`, its own ``title``, ``alt``,
        ``focal_point`` and ``tags`` are sent, overridden by any passed here, and its
        ``etag`` becomes ``If-Match``. Given an id, what is passed is all there is, and
        ``if_match`` or ``force=True`` is required.
        """
        if isinstance(asset, m.Asset):
            asset_id = asset.id
            body = _asset_details(
                title=title if title is not None else asset.title,
                alt=alt if alt is not None else asset.alt,
                focal_point=focal_point if focal_point is not None else asset.focal_point,
                tags=tags if tags is not None else asset.tags,
            )
            etag = if_match or asset.etag
        else:
            asset_id = asset
            body = _asset_details(title=title, alt=alt, focal_point=focal_point, tags=tags)
            etag = if_match
        etag = self._precondition(asset_id, etag, force, "an update")
        resp = self._t.put(f"{self._r.path}/assets/{segment(asset_id)}", body, if_match=etag)
        return m.Asset.from_dict(resp.body, etag=resp.etag)

    def delete(
        self, asset: m.Asset | str, *, if_match: str | None = None, force: bool = False
    ) -> str | None:
        """Tombstone an asset on this branch. Returns the tombstone's ETag.

        ⚠️ The bytes stay, and the asset's URL keeps working: history and released tags
        still point at it.
        """
        asset_id = asset.id if isinstance(asset, m.Asset) else asset
        etag = if_match or (asset.etag if isinstance(asset, m.Asset) else None)
        etag = self._precondition(asset_id, etag, force, "a delete")
        resp: Response = self._t.delete(f"{self._r.path}/assets/{segment(asset_id)}", if_match=etag)
        return resp.etag

    def _precondition(self, asset_id: str, etag: str | None, force: bool, what: str) -> str:
        if etag is not None:
            return etag
        if not force:
            raise PreconditionRequired(
                f"{what} needs the ETag from the read it is based on; pass if_match=…, "
                "or force=True to act on the current version",
                code="PRECONDITION_REQUIRED",
                status=428,
            )
        current = self.get(asset_id).etag
        if current is None:
            raise PreconditionRequired(
                "the server returned no ETag", code="PRECONDITION_REQUIRED", status=428
            )
        return current


def sha256_of(source: AssetSource) -> tuple[str, int]:
    """The SHA-256, as lower-case hex, and the size of a file or of bytes. Streams a file."""
    digest = hashlib.sha256()
    if isinstance(source, bytes):
        digest.update(source)
        return digest.hexdigest(), len(source)
    size = 0
    for chunk in _chunks(source):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _chunks(path: str | os.PathLike[str]) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            yield chunk


def _asset_details(
    *,
    title: str | None,
    alt: str | None,
    focal_point: m.FocalPoint | None,
    tags: builtins.list[str] | None,
) -> JSON:
    body: JSON = {}
    if title is not None:
        body["title"] = title
    if alt is not None:
        body["alt"] = alt
    if focal_point is not None:
        body["focalPoint"] = focal_point.to_dict()
    if tags:
        body["tags"] = builtins.list(tags)
    return body


__all__ = ["Coho", "Account", "Project", "Ref", "Preview", "AssetsApi", "sha256_of"]
