"""Typed views over the JSON the tiers return.

Every model keeps the original document in ``raw`` so nothing is lost between the
wire and the caller, and so the CLI's ``--output json`` prints exactly what the
contract describes. Values that carry an ``ETag`` (`Entry`, `ContentType`)
remember it, and a ``put`` of them sends ``If-Match`` automatically.

Field names follow the contracts (camelCase on the wire becomes snake_case here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JSON = dict[str, Any]


def _list(raw: JSON, key: str) -> list[JSON]:
    value = raw.get(key)
    return list(value) if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Session, accounts, members
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Membership:
    """One row of ``GET /api/v1/me``'s ``accounts``: an account the user belongs to."""

    account_id: str
    account_name: str
    role: str
    """``admin`` or ``member`` — the account-level role, not a project role."""
    actor_id: str | None = None
    """Present once the user has acted in the account. Not a credential."""
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Membership:
        return cls(
            account_id=str(raw.get("accountId", "")),
            account_name=str(raw.get("accountName", "")),
            role=str(raw.get("role", "member")),
            actor_id=raw.get("actorId"),
            raw=raw,
        )


@dataclass(slots=True)
class Me:
    """Who is signed in, and where."""

    user_id: str
    display_name: str | None
    email: str | None
    accounts: list[Membership]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Me:
        user = raw.get("user") or {}
        return cls(
            user_id=str(user.get("id", "")),
            display_name=user.get("displayName"),
            email=user.get("email"),
            accounts=[Membership.from_dict(a) for a in _list(raw, "accounts")],
            raw=raw,
        )

    def membership(self, account: str) -> Membership | None:
        """By id, or by name (case-insensitive)."""
        for m in self.accounts:
            if m.account_id == account:
                return m
        lowered = account.lower()
        for m in self.accounts:
            if m.account_name.lower() == lowered:
                return m
        return None


@dataclass(slots=True)
class AccountInfo:
    id: str
    name: str
    status: str | None = None
    plan: str | None = None
    created_at: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> AccountInfo:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            status=raw.get("status"),
            plan=raw.get("plan"),
            created_at=raw.get("createdAt"),
            raw=raw,
        )


@dataclass(slots=True)
class Member:
    """A user in an account, with their account-level role."""

    id: str
    display_name: str | None
    email: str | None
    role: str
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Member:
        return cls(
            id=str(raw.get("id", "")),
            display_name=raw.get("displayName"),
            email=raw.get("email"),
            role=str(raw.get("role", "member")),
            raw=raw,
        )


@dataclass(slots=True)
class Invitation:
    id: str
    account_id: str
    account_name: str | None
    email: str
    role: str
    status: str
    """``pending``, ``accepted``, ``revoked`` or ``expired``."""
    created_at: str | None
    expires_at: str | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Invitation:
        return cls(
            id=str(raw.get("id", "")),
            account_id=str(raw.get("accountId", "")),
            account_name=raw.get("accountName"),
            email=str(raw.get("email", "")),
            role=str(raw.get("role", "member")),
            status=str(raw.get("status", "pending")),
            created_at=raw.get("createdAt"),
            expires_at=raw.get("expiresAt"),
            raw=raw,
        )


@dataclass(slots=True)
class IssuedInvitation:
    """The answer to creating an invitation. ⚠️ ``token`` is shown once and never again."""

    invitation: Invitation
    token: str
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> IssuedInvitation:
        return cls(
            invitation=Invitation.from_dict(raw.get("invitation") or {}),
            token=str(raw.get("token", "")),
            raw=raw,
        )

    def link(self, public_url: str) -> str:
        """The invitation link a person clicks: the token rides in the URL fragment."""
        return f"{public_url.rstrip('/')}/invite#{self.token}"


@dataclass(slots=True)
class Entitlements:
    """What the plan allows. An absent limit means **unlimited**."""

    plan: str | None
    plan_status: str | None
    max_projects: int | None
    max_live_environments: int | None
    max_members: int | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Entitlements:
        return cls(
            plan=raw.get("plan"),
            plan_status=raw.get("planStatus"),
            max_projects=raw.get("maxProjects"),
            max_live_environments=raw.get("maxLiveEnvironments"),
            max_members=raw.get("maxMembers"),
            raw=raw,
        )


@dataclass(slots=True)
class Plan:
    id: str
    name: str
    version: int | None
    status: str | None
    max_projects: int | None
    max_live_environments: int | None
    max_members: int | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Plan:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            version=raw.get("version"),
            status=raw.get("status"),
            max_projects=raw.get("maxProjects"),
            max_live_environments=raw.get("maxLiveEnvironments"),
            max_members=raw.get("maxMembers"),
            raw=raw,
        )


@dataclass(slots=True)
class AuditEvent:
    id: str
    action: str
    account_id: str | None
    by_user_id: str | None
    by_operator: bool
    subject_user_id: str | None
    subject_id: str | None
    detail: JSON
    created_at: str | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> AuditEvent:
        return cls(
            id=str(raw.get("id", "")),
            action=str(raw.get("action", "")),
            account_id=raw.get("accountId"),
            by_user_id=raw.get("byUserId"),
            by_operator=bool(raw.get("byOperator", False)),
            subject_user_id=raw.get("subjectUserId"),
            subject_id=raw.get("subjectId"),
            detail=dict(raw.get("detail") or {}),
            created_at=raw.get("createdAt"),
            raw=raw,
        )


@dataclass(slots=True)
class Login:
    """An identity attached to a user."""

    id: str
    provider: str
    subject: str
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Login:
        return cls(
            id=str(raw.get("id", "")),
            provider=str(raw.get("provider", "")),
            subject=str(raw.get("subject", "")),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Projects, refs, branches, tags
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProjectInfo:
    id: str
    name: str
    trunk: str
    environments: list[JSON]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> ProjectInfo:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            trunk=str(raw.get("trunk", "")),
            environments=_list(raw, "environments"),
            raw=raw,
        )


@dataclass(slots=True)
class RefInfo:
    """A branch, tag or environment — one namespace."""

    name: str
    kind: str
    """``version_branch``, ``feature_branch``, ``tag`` or ``environment``."""
    target: str | None = None
    description: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> RefInfo:
        return cls(
            name=str(raw.get("name", "")),
            kind=str(raw.get("kind", "")),
            target=raw.get("target"),
            description=raw.get("description"),
            raw=raw,
        )


@dataclass(slots=True)
class Branch:
    name: str
    kind: str
    status: str | None = None
    parent: str | None = None
    depth: int | None = None
    branched_at: int | None = None
    depth_warning: bool = False
    pending_forward_port: int | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Branch:
        return cls(
            name=str(raw.get("name", "")),
            kind=str(raw.get("kind", "")),
            status=raw.get("status"),
            parent=raw.get("parent") or raw.get("from"),
            depth=raw.get("depth"),
            branched_at=raw.get("branchedAt"),
            depth_warning=bool(raw.get("depthWarning", False)),
            pending_forward_port=raw.get("pendingForwardPort"),
            raw=raw,
        )


@dataclass(slots=True)
class Warning:
    """A non-blocking advisory: ``SKIPPED_TIER`` or ``PENDING_FORWARD_PORT``."""

    code: str
    message: str

    @classmethod
    def from_dict(cls, raw: JSON) -> Warning:
        return cls(code=str(raw.get("code", "")), message=str(raw.get("message", "")))


@dataclass(slots=True)
class Tag:
    name: str
    ref: str
    """The version branch it was cut from."""
    at: int
    """The frozen sequence. A tag *is* ``(ref, at)``."""
    warnings: list[Warning] = field(default_factory=list)
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Tag:
        return cls(
            name=str(raw.get("name", "")),
            ref=str(raw.get("ref", "")),
            at=int(raw.get("at", 0)),
            warnings=[Warning.from_dict(w) for w in _list(raw, "warnings")],
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContentType:
    """A content type as resolved on a ref. Carries its ``etag`` for the next ``put``."""

    slug: str
    version: str
    definition: JSON
    etag: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON, etag: str | None = None) -> ContentType:
        return cls(
            slug=str(raw.get("slug", "")),
            version=str(raw.get("version", "")),
            definition=dict(raw.get("definition") or {}),
            etag=etag,
            raw=raw,
        )

    @property
    def name(self) -> str | None:
        return self.definition.get("_name") or self.definition.get("name")

    @property
    def fields(self) -> list[JSON]:
        return _list(self.definition, "fields")


@dataclass(slots=True)
class Entry:
    """An entry as resolved on a ref. Carries its ``etag`` for the next ``put``/``delete``."""

    id: str
    type: str
    slug: str
    version: str
    fields: JSON
    etag: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON, etag: str | None = None) -> Entry:
        return cls(
            id=str(raw.get("id", "")),
            type=str(raw.get("type", "")),
            slug=str(raw.get("slug", "")),
            version=str(raw.get("version", "")),
            fields=dict(raw.get("fields") or {}),
            etag=etag,
            raw=raw,
        )


@dataclass(slots=True)
class Page:
    limit: int
    offset: int
    has_more: bool

    @classmethod
    def from_dict(cls, raw: JSON) -> Page:
        return cls(
            limit=int(raw.get("limit", 0)),
            offset=int(raw.get("offset", 0)),
            has_more=bool(raw.get("hasMore", False)),
        )


@dataclass(slots=True)
class EntryPage:
    entries: list[Entry]
    page: Page
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> EntryPage:
        return cls(
            entries=[Entry.from_dict(e) for e in _list(raw, "entries")],
            page=Page.from_dict(raw.get("page") or {}),
            raw=raw,
        )


@dataclass(slots=True)
class HistoryEntry:
    version: str
    parent: str | None
    seq: int
    op: str
    """``upsert`` or ``tombstone``."""
    actor: str
    at: str
    branch: str
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> HistoryEntry:
        return cls(
            version=str(raw.get("version", "")),
            parent=raw.get("parent"),
            seq=int(raw.get("seq", 0)),
            op=str(raw.get("op", "")),
            actor=str(raw.get("actor", "")),
            at=str(raw.get("at", "")),
            branch=str(raw.get("branch", "")),
            raw=raw,
        )


@dataclass(slots=True)
class History:
    id: str
    ref: str
    versions: list[HistoryEntry]
    page: Page
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> History:
        return cls(
            id=str(raw.get("id", "")),
            ref=str(raw.get("ref", "")),
            versions=[HistoryEntry.from_dict(v) for v in _list(raw, "versions")],
            page=Page.from_dict(raw.get("page") or {}),
            raw=raw,
        )


@dataclass(slots=True)
class IncomingReference:
    node_id: str
    kind: str
    slug: str | None
    field: str

    @classmethod
    def from_dict(cls, raw: JSON) -> IncomingReference:
        return cls(
            node_id=str(raw.get("nodeId", "")),
            kind=str(raw.get("kind", "")),
            slug=raw.get("slug"),
            field=str(raw.get("field", "")),
        )


@dataclass(slots=True)
class References:
    """What points at a node, on a ref. Complete, never paginated."""

    id: str
    ref: str
    incoming: list[IncomingReference]
    total: int
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> References:
        return cls(
            id=str(raw.get("id", "")),
            ref=str(raw.get("ref", "")),
            incoming=[IncomingReference.from_dict(i) for i in _list(raw, "incoming")],
            total=int(raw.get("total", 0)),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Diff and merge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Conflict:
    node_id: str
    reason: str
    slug: str | None = None
    paths: list[str] = field(default_factory=list)
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Conflict:
        return cls(
            node_id=str(raw.get("nodeId", "")),
            reason=str(raw.get("reason", "")),
            slug=raw.get("slug"),
            paths=[str(p) for p in _list(raw, "paths")],
            raw=raw,
        )


@dataclass(slots=True)
class NodeDiff:
    node_id: str
    kind: str
    change: str
    slug: str | None = None
    fields: list[JSON] = field(default_factory=list)
    schema_changes: list[JSON] = field(default_factory=list)
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> NodeDiff:
        return cls(
            node_id=str(raw.get("nodeId", "")),
            kind=str(raw.get("kind", "")),
            change=str(raw.get("change", "")),
            slug=raw.get("slug"),
            fields=_list(raw, "fields"),
            schema_changes=_list(raw, "schemaChanges"),
            raw=raw,
        )


@dataclass(slots=True)
class Diff:
    from_ref: str
    to_ref: str
    mode: str
    added: int
    modified: int
    deleted: int
    nodes: list[NodeDiff]
    conflicts: list[Conflict]
    merge_token: str | None
    base: JSON | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Diff:
        summary = raw.get("summary") or {}
        return cls(
            from_ref=str(raw.get("from", "")),
            to_ref=str(raw.get("to", "")),
            mode=str(raw.get("mode", "")),
            added=int(summary.get("added", 0)),
            modified=int(summary.get("modified", 0)),
            deleted=int(summary.get("deleted", 0)),
            nodes=[NodeDiff.from_dict(n) for n in _list(raw, "nodes")],
            conflicts=[Conflict.from_dict(c) for c in _list(raw, "conflicts")],
            merge_token=raw.get("mergeToken"),
            base=raw.get("base"),
            raw=raw,
        )


@dataclass(slots=True)
class Resolution:
    """One answer to a conflict: take ``source``, ``target`` or a literal ``value``."""

    node_id: str
    take: str
    path: str | None = None
    value: Any = None

    def to_dict(self) -> JSON:
        out: JSON = {"nodeId": self.node_id, "take": self.take}
        if self.path is not None:
            out["path"] = self.path
        if self.take == "value":
            out["value"] = self.value
        return out

    @classmethod
    def from_dict(cls, raw: JSON) -> Resolution:
        return cls(
            node_id=str(raw["nodeId"]),
            take=str(raw["take"]),
            path=raw.get("path"),
            value=raw.get("value"),
        )


@dataclass(slots=True)
class MergeResult:
    source: str
    target: str
    merged: int
    unchanged: int
    resolved: int
    base: JSON | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> MergeResult:
        return cls(
            source=str(raw.get("source", "")),
            target=str(raw.get("target", "")),
            merged=int(raw.get("merged", 0)),
            unchanged=int(raw.get("unchanged", 0)),
            resolved=int(raw.get("resolved", 0)),
            base=raw.get("base"),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Environments, tiers, promotion
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Environment:
    name: str
    tier: str
    target: str | None = None
    resolves: str | None = None
    """``live`` (points at branch tips) or ``snapshot`` (points at tags)."""
    previous_target: str | None = None
    warnings: list[Warning] = field(default_factory=list)
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Environment:
        return cls(
            name=str(raw.get("name", "")),
            tier=str(raw.get("tier", "")),
            target=raw.get("target"),
            resolves=raw.get("resolves"),
            previous_target=raw.get("previousTarget"),
            warnings=[Warning.from_dict(w) for w in _list(raw, "warnings")],
            raw=raw,
        )


@dataclass(slots=True)
class RefHistoryEntry:
    ref: str
    from_target: str | None
    to_target: str | None
    reason: str | None
    actor: str
    at: str
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> RefHistoryEntry:
        return cls(
            ref=str(raw.get("ref", "")),
            from_target=raw.get("from"),
            to_target=raw.get("to"),
            reason=raw.get("reason"),
            actor=str(raw.get("actor", "")),
            at=str(raw.get("at", "")),
            raw=raw,
        )


@dataclass(slots=True)
class RefHistory:
    ref: str
    history: list[RefHistoryEntry]
    has_more: bool
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> RefHistory:
        return cls(
            ref=str(raw.get("ref", "")),
            history=[RefHistoryEntry.from_dict(h) for h in _list(raw, "history")],
            has_more=bool(raw.get("hasMore", False)),
            raw=raw,
        )


@dataclass(slots=True)
class Tier:
    id: str
    resolves: str
    ord: int
    promote_permission: str
    environments: list[str]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> Tier:
        return cls(
            id=str(raw.get("id", "")),
            resolves=str(raw.get("resolves", "")),
            ord=int(raw.get("ord", 0)),
            promote_permission=str(raw.get("promotePermission", "")),
            environments=[str(e) for e in _list(raw, "environments")],
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Roles and delivery keys
# ---------------------------------------------------------------------------

PROJECT_ROLES = ("viewer", "author", "maintainer", "release_manager", "owner")
ACCOUNT_ROLES = ("admin", "member")


@dataclass(slots=True)
class ProjectRole:
    actor_id: str
    role: str
    granted_by: str | None
    granted_at: str | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> ProjectRole:
        return cls(
            actor_id=str(raw.get("actorId", "")),
            role=str(raw.get("role", "")),
            granted_by=raw.get("grantedBy"),
            granted_at=raw.get("grantedAt"),
            raw=raw,
        )


@dataclass(slots=True)
class DeliveryKey:
    """Metadata about a key. Never the key itself, never its hash."""

    id: str
    label: str | None
    refs: list[str] | None
    """``None`` means every ref in the project."""
    created_at: str | None
    expires_at: str | None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> DeliveryKey:
        refs = raw.get("refs")
        return cls(
            id=str(raw.get("id", "")),
            label=raw.get("label"),
            refs=[str(r) for r in refs] if isinstance(refs, list) else None,
            created_at=raw.get("createdAt"),
            expires_at=raw.get("expiresAt"),
            raw=raw,
        )


@dataclass(slots=True)
class IssuedDeliveryKey:
    """⚠️ ``key`` is in this response and in no other, ever. Only its hash is stored."""

    key: str
    delivery: DeliveryKey
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> IssuedDeliveryKey:
        return cls(
            key=str(raw.get("key", "")),
            delivery=DeliveryKey.from_dict(raw.get("delivery") or {}),
            raw=raw,
        )


@dataclass(slots=True)
class DeliveryKeyList:
    keys: list[DeliveryKey]
    public_refs: list[str]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> DeliveryKeyList:
        return cls(
            keys=[DeliveryKey.from_dict(k) for k in _list(raw, "keys")],
            public_refs=[str(r) for r in _list(raw, "publicRefs")],
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Export:
    """The outcome of an export: where the bytes went, and the coordinate they represent."""

    ref: str
    format: str
    path: str | None
    """The local file written, or ``None`` when streamed to a caller-supplied sink."""
    seq: int | None
    """``X-Coho-Export-Seq`` — load-bearing for a branch, whose tip moves."""
    bytes_written: int
    redirected_to: str | None = None
    """The presigned URL a tag export was served from, if any."""
    manifest: JSON | None = None
