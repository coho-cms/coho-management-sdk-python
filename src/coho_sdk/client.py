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
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

import httpx

from . import models as m
from .auth import TokenProvider, token_store_for
from .errors import CohoError, NotFound, PreconditionRequired, SnapshotNotReady
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

    def account(self, account: str) -> Account:
        """By id or by name. A name is resolved through ``/me``; an id is used as-is."""
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

    def signup_start(self, account_name: str, *, display_name: str | None = None) -> str:
        """Start founding a new account. Returns the URL to open; the BFF finishes the flow."""
        body = {"accountName": account_name, "displayName": display_name}
        resp = self.transport.post("/auth/signup", {k: v for k, v in body.items() if v})
        return str(resp.body["location"])

    def invitation_lookup(self, token: str) -> m.Invitation:
        """What an invitation offers. Holding the token is the credential; no login needed."""
        resp = self.transport.post("/auth/invitations/lookup", {"invitation": token})
        return m.Invitation.from_dict(resp.body or {})

    def invitation_accept_start(self, token: str, *, display_name: str | None = None) -> str:
        """Start accepting an invitation. Returns the URL to open in a browser."""
        body = {"invitation": token, "displayName": display_name}
        resp = self.transport.post("/auth/invitations/accept", {k: v for k, v in body.items() if v})
        return str(resp.body["location"])


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
    """Create and open projects. ⚠️ There is no server-side listing yet (doc 04 §9)."""

    def __init__(self, account: Account) -> None:
        self._a = account
        self._t = account._t

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

    def info(self, *, refresh: bool = False) -> m.ProjectInfo:
        if self._info is None or refresh:
            self._info = m.ProjectInfo.from_dict(self._t.get(self.path).body)
        return self._info

    @property
    def name(self) -> str:
        return self.info().name

    def ref(self, name: str) -> Ref:
        return Ref(self, name)

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
        self, name: str, *, branch: str, description: str | None = None, at: int | None = None
    ) -> m.Tag:
        """Cut an immutable tag on a **version** branch. Does not merge, does not validate."""
        body: JSON = {"name": name, "ref": branch}
        if description:
            body["description"] = description
        if at is not None:
            body["at"] = at
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
        """``owner`` only. ``resolves`` (``live``/``snapshot``) is immutable afterwards."""
        body = {"id": tier_id, "resolves": resolves, "ord": ord}
        return m.Tier.from_dict(self._t.post(f"{self._p.path}/tiers", body).body)

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
    """A branch, tag or environment, as a place to read and (for a branch) write content."""

    def __init__(self, project: Project, name: str) -> None:
        self.project = project
        self.name = name
        self._t = project._t
        self.path = f"{project.path}/refs/{segment(name)}"
        self.types = TypesApi(self)
        self.entries = EntriesApi(self)

    def info(self) -> m.RefInfo:
        return self.project.refs.get(self.name)

    def export(self, **kw: Any) -> m.Export:
        return self.project.export(self.name, **kw)

    def preview(self) -> Preview:
        return self.project.preview(self.name)


class TypesApi:
    def __init__(self, ref: Ref) -> None:
        self._r = ref
        self._t = ref._t

    def list(self) -> list[m.ContentType]:
        body = self._t.get(f"{self._r.path}/types").body
        return [m.ContentType.from_dict(t) for t in (body or {}).get("types", [])]

    def get(self, slug: str) -> m.ContentType:
        resp = self._t.get(f"{self._r.path}/types/{segment(slug)}")
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


__all__ = ["Coho", "Account", "Project", "Ref", "Preview"]
