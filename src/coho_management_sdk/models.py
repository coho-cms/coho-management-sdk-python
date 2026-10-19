"""Typed views over the JSON the tiers return.

Every model keeps the original document in ``raw`` so nothing is lost between the
wire and the caller, and so the CLI's ``--output json`` prints exactly what the
contract describes. Values that carry an ``ETag`` (`Entry`, `ContentType`)
remember it, and a ``put`` of them sends ``If-Match`` automatically.

Field names follow the contracts (camelCase on the wire becomes snake_case here).
"""

from __future__ import annotations

from collections.abc import Callable
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
class ProjectSummary:
    """One row of ``GET .../projects``: a project you can open, and your role on it."""

    id: str
    name: str
    role: str
    """Your project role, e.g. ``owner`` (every project, for an account admin) or ``author``."""
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> ProjectSummary:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            role=str(raw.get("role", "")),
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

    @property
    def kind(self) -> str:
        """``content``, or ``component`` for the items of a repeater. Fixed once the type exists.

        Servers before typed repeaters send no ``kind``; their types are all content types.
        """
        return str(self.raw.get("kind") or self.definition.get("kind") or "content")

    @property
    def extends(self) -> str | None:
        """The slug of the type this one extends, or ``None`` for a root."""
        parent = self.definition.get("extends")
        return parent if isinstance(parent, str) else None

    @property
    def abstract(self) -> bool:
        """Whether no entry is of this type itself, only of the types that extend it."""
        return self.definition.get("abstract") is True

    @property
    def traits(self) -> list[str]:
        """The traits it takes itself, by slug, in order. Its ancestors' are theirs."""
        return [str(t) for t in _list(self.definition, "traits")]


@dataclass(slots=True)
class Trait:
    """A trait: a named group of fields that types take (doc 05 §10). Never a type.

    Carries its ``etag`` for the next ``put``.
    """

    slug: str
    version: str
    definition: JSON
    etag: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON, etag: str | None = None) -> Trait:
        return cls(
            slug=str(raw.get("slug", "")),
            version=str(raw.get("version", "")),
            definition=dict(raw.get("definition") or {}),
            etag=etag,
            raw=raw,
        )

    @property
    def name(self) -> str | None:
        return self.definition.get("_name")

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
# Assets
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FocalPoint:
    """Where a crop centres, as fractions of the image's width and height (``0..1``)."""

    x: float
    y: float

    def to_dict(self) -> JSON:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_dict(cls, raw: JSON) -> FocalPoint:
        return cls(x=float(raw.get("x", 0.5)), y=float(raw.get("y", 0.5)))


@dataclass(slots=True)
class AssetFile:
    """The bytes an asset points at, as the server sniffed them. Never set by a client."""

    sha256: str
    byte_size: int
    media_type: str
    filename: str
    width: int | None = None
    """Raster images only, as is ``height``."""
    height: int | None = None

    @classmethod
    def from_dict(cls, raw: JSON) -> AssetFile:
        return cls(
            sha256=str(raw.get("sha256", "")),
            byte_size=int(raw.get("byteSize", 0)),
            media_type=str(raw.get("mediaType", "")),
            filename=str(raw.get("filename", "")),
            width=raw.get("width"),
            height=raw.get("height"),
        )


@dataclass(slots=True)
class Asset:
    """An asset as resolved on a ref. Carries its ``etag`` for the next ``put``/``delete``.

    ⚠️ ``url`` and ``image_url`` are public to anyone holding them, drafts included, and
    deleting the asset does not stop them working (doc 40 §7.0).
    """

    id: str
    slug: str
    version: str
    file: AssetFile
    title: str | None = None
    alt: str | None = None
    focal_point: FocalPoint | None = None
    tags: list[str] = field(default_factory=list)
    url: str | None = None
    """The original, from the asset CDN."""
    image_url: str | None = None
    """Raster images only: append ``?w=…`` and friends to resize on request."""
    etag: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON, etag: str | None = None) -> Asset:
        focal = raw.get("focalPoint")
        return cls(
            id=str(raw.get("id", "")),
            slug=str(raw.get("slug", "")),
            version=str(raw.get("version", "")),
            file=AssetFile.from_dict(raw.get("file") or {}),
            title=raw.get("title"),
            alt=raw.get("alt"),
            focal_point=FocalPoint.from_dict(focal) if isinstance(focal, dict) else None,
            tags=[str(t) for t in raw.get("tags") or []],
            url=raw.get("url"),
            image_url=raw.get("imageUrl"),
            etag=etag or (f'"{raw["version"]}"' if raw.get("version") else None),
            raw=raw,
        )


@dataclass(slots=True)
class AssetPage:
    assets: list[Asset]
    page: Page
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> AssetPage:
        return cls(
            assets=[Asset.from_dict(a) for a in _list(raw, "assets")],
            page=Page.from_dict(raw.get("page") or {}),
            raw=raw,
        )


@dataclass(slots=True)
class PresignedPut:
    """Where to PUT an upload's bytes. Send every header, exactly: they are signed."""

    url: str
    method: str
    headers: dict[str, str]
    expires_at: str

    @classmethod
    def from_dict(cls, raw: JSON) -> PresignedPut:
        return cls(
            url=str(raw.get("url", "")),
            method=str(raw.get("method", "PUT")),
            headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
            expires_at=str(raw.get("expiresAt", "")),
        )


@dataclass(slots=True)
class AssetUpload:
    """A started upload. ``put`` is ``None`` when the project already holds the bytes."""

    id: str
    expires_at: str
    put: PresignedPut | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> AssetUpload:
        upload = raw.get("upload")
        return cls(
            id=str(raw.get("id", "")),
            expires_at=str(raw.get("expiresAt", "")),
            put=PresignedPut.from_dict(upload) if isinstance(upload, dict) else None,
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
class ProjectToken:
    """A project token's metadata (doc 29). Never the token, never its hash."""

    id: str
    """Also the id it signs content with, so history names it."""
    label: str
    role: str
    state: str
    """``active``, ``expired`` or ``revoked``."""
    created_by: str
    created_at: str
    expires_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> ProjectToken:
        return cls(
            id=str(raw.get("id", "")),
            label=str(raw.get("label", "")),
            role=str(raw.get("role", "")),
            state=str(raw.get("state", "")),
            created_by=str(raw.get("createdBy", "")),
            created_at=str(raw.get("createdAt", "")),
            expires_at=str(raw.get("expiresAt", "")),
            last_used_at=raw.get("lastUsedAt"),
            revoked_at=raw.get("revokedAt"),
            revoked_by=raw.get("revokedBy"),
            raw=raw,
        )


@dataclass(slots=True)
class CreatedProjectToken:
    """⚠️ ``token`` is in this response and in no other, ever. Only its hash is stored."""

    token: str
    project_token: ProjectToken
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> CreatedProjectToken:
        return cls(
            token=str(raw.get("token", "")),
            project_token=ProjectToken.from_dict(raw.get("projectToken") or {}),
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
# Usage (coho-data doc 41)
# ---------------------------------------------------------------------------

Dims = dict[str, str]
Where = Callable[[Dims], bool]


def _everything(dims: Dims) -> bool:
    return True


@dataclass(slots=True)
class UsageSeries:
    """One combination of a meter's dimensions: delivery by ``key`` and ``outcome``, say."""

    dims: Dims
    values: list[int]
    """One per bucket, lined up with `UsageReport.buckets`."""
    total: int
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> UsageSeries:
        dims = raw.get("dims") or {}
        values = raw.get("values")
        return cls(
            dims={str(k): str(v) for k, v in dims.items()},
            values=[int(v) for v in values] if isinstance(values, list) else [],
            total=int(raw.get("total", 0)),
            raw=raw,
        )


@dataclass(slots=True)
class UsageMeter:
    meter: str
    """``delivery.requests``, ``assets.stored_bytes`` … (doc 41 §2)."""
    kind: str
    """How a total is formed: ``flow`` sums, ``peak`` and ``distinct`` take the largest
    period, ``stock`` the latest."""
    unit: str
    series: list[UsageSeries]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> UsageMeter:
        return cls(
            meter=str(raw.get("meter", "")),
            kind=str(raw.get("kind", "flow")),
            unit=str(raw.get("unit", "")),
            series=[UsageSeries.from_dict(s) for s in _list(raw, "series")],
            raw=raw,
        )


@dataclass(slots=True)
class KeyUsage:
    """One delivery key's traffic over a report's range, and how well its client caches."""

    key: str
    """A delivery key's id, or ``none`` for a keyless read."""
    access: str
    """``key`` or ``public`` (a keyless read of an open environment)."""
    requests: int
    not_modified: int
    refused: int
    bytes: int
    ratio: float | None
    """Answered requests per distinct URL per period. Near 1 is a client that caches; in
    the tens or hundreds, one asking for the same thing again. ``None`` with no URLs."""


@dataclass(slots=True)
class UsageReport:
    """A project's usage, or the account's summed, hour by hour or day by day.

    The arithmetic is the portal's, so both show the same numbers: `total` forms a total
    the way the meter's kind says, after summing the series a ``where`` keeps.
    """

    start: str
    """``from`` on the wire: inclusive, a whole period."""
    end: str
    """``to`` on the wire: exclusive."""
    grain: str
    """``hour`` or ``day``."""
    buckets: list[str]
    meters: list[UsageMeter]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> UsageReport:
        return cls(
            start=str(raw.get("from", "")),
            end=str(raw.get("to", "")),
            grain=str(raw.get("grain", "hour")),
            buckets=[str(b) for b in _list(raw, "buckets")],
            meters=[UsageMeter.from_dict(m) for m in _list(raw, "meters")],
            raw=raw,
        )

    def meter(self, name: str) -> UsageMeter | None:
        return next((m for m in self.meters if m.meter == name), None)

    def per_bucket(self, name: str, where: Where = _everything) -> list[int]:
        """The meter's series summed per bucket, over the series ``where`` keeps."""
        out = [0] * len(self.buckets)
        meter = self.meter(name)
        for series in meter.series if meter else []:
            if where(series.dims):
                for i, v in enumerate(series.values[: len(out)]):
                    out[i] += v
        return out

    def total(self, name: str, where: Where = _everything) -> int:
        """A flow's sum; a peak's or distinct count's largest period; a stock's latest value.

        Series are summed first, so a stock held in two tiers is the two together. An
        absent meter is 0.
        """
        meter = self.meter(name)
        if meter is None:
            return 0
        values = self.per_bucket(name, where)
        if meter.kind == "flow":
            return sum(values)
        if meter.kind == "stock":
            return values[-1] if values else 0
        return max(values, default=0)

    def totals_by(self, name: str, dim: str, where: Where = _everything) -> dict[str, int]:
        """A flow's total split by one dimension: ``management.requests`` by ``class``."""
        out: dict[str, int] = {}
        meter = self.meter(name)
        for series in meter.series if meter else []:
            if where(series.dims):
                value = series.dims.get(dim, "")
                out[value] = out.get(value, 0) + series.total
        return out

    def delivery_by_key(self) -> list[KeyUsage]:
        """Delivery per key, busiest first: the table that finds the client not caching."""
        keys: dict[str, KeyUsage] = {}
        answered: dict[str, int] = {}
        urls: dict[str, int] = {}

        def row(dims: Dims) -> KeyUsage:
            key = dims.get("key", "none")
            if key not in keys:
                keys[key] = KeyUsage(key, dims.get("access", "none"), 0, 0, 0, 0, None)
            return keys[key]

        requests = self.meter("delivery.requests")
        for s in requests.series if requests else []:
            usage = row(s.dims)
            usage.requests += s.total
            outcome = s.dims.get("outcome")
            if outcome == "not_modified":
                usage.not_modified += s.total
            if outcome == "refused":
                usage.refused += s.total
            if outcome in ("ok", "not_modified"):
                answered[usage.key] = answered.get(usage.key, 0) + s.total
        sent = self.meter("delivery.bytes")
        for s in sent.series if sent else []:
            row(s.dims).bytes += s.total
        # Distinct URLs are counted per period, so each period's count is summed: a client
        # that fetched one URL once an hour for a day saw 24, and asked 24 times.
        distinct = self.meter("delivery.distinct_urls")
        for s in distinct.series if distinct else []:
            key = s.dims.get("key", "none")
            urls[key] = urls.get(key, 0) + sum(s.values)

        for usage in keys.values():
            if urls.get(usage.key, 0) > 0:
                usage.ratio = answered.get(usage.key, 0) / urls[usage.key]
        return sorted(keys.values(), key=lambda u: (-u.requests, u.key))


@dataclass(slots=True)
class ProjectUsageTotals:
    """One project's line in the account's report: each meter's total, by name."""

    id: str
    name: str
    totals: dict[str, int]
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: JSON) -> ProjectUsageTotals:
        totals = raw.get("totals") or {}
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            totals={str(k): int(v) for k, v in totals.items()},
            raw=raw,
        )


@dataclass(slots=True)
class AccountUsageReport(UsageReport):
    """The account's usage: every project's series summed, dimensions collapsed, and each
    project's totals."""

    projects: list[ProjectUsageTotals] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: JSON) -> AccountUsageReport:
        base = UsageReport.from_dict(raw)
        return cls(
            start=base.start,
            end=base.end,
            grain=base.grain,
            buckets=base.buckets,
            meters=base.meters,
            raw=raw,
            projects=[ProjectUsageTotals.from_dict(p) for p in _list(raw, "projects")],
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
