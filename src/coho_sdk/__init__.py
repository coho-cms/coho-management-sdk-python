"""coho_sdk — the management SDK for Coho.

    from coho_sdk import Coho

    coho = Coho.from_profile("staging")            # or Coho(url=..., token=...)
    site = coho.account("acme").project("0192…")
    dev = site.ref("dev")
    post = dev.entries.get("0192…")
    post.fields["title"]["en-US"] = "New title"
    dev.entries.put(post)                          # sends If-Match from post.etag

Three rules:

* **Errors are the ``code``.** Every failure raises `CohoError` with ``.code``;
  subclasses exist for the codes a caller branches on. Never match on status alone.
* **The ETag is carried by the object.** A ``get`` returns a value that remembers
  its ETag; a ``put`` of it sends ``If-Match``. Overwrite blind with ``force=True``.
* **The CLI is a thin layer over this.** Anything ``coho`` does is one call here.
"""

from ._version import __version__
from .auth import TokenProvider, TokenSet, login
from .client import Account, Coho, Preview, Project, Ref
from .errors import (
    AccountAdminRequired,
    AuthError,
    CohoError,
    EnvironmentMoved,
    Forbidden,
    MergeConflict,
    NotFound,
    NotLoggedIn,
    PlanLimit,
    PreconditionRequired,
    SnapshotNotReady,
    TransportError,
    Unauthenticated,
    ValidationFailed,
    VersionConflict,
)
from .models import (
    Branch,
    Conflict,
    ContentType,
    DeliveryKey,
    Diff,
    Entry,
    Environment,
    Invitation,
    IssuedDeliveryKey,
    IssuedInvitation,
    Me,
    Member,
    MergeResult,
    ProjectInfo,
    ProjectRole,
    RefInfo,
    Resolution,
    Tag,
    Tier,
)
from .profiles import Config, Context, Profile

__all__ = [
    "__version__",
    "Coho",
    "Account",
    "Project",
    "Ref",
    "Preview",
    "Config",
    "Context",
    "Profile",
    "TokenProvider",
    "TokenSet",
    "login",
    "CohoError",
    "AuthError",
    "NotLoggedIn",
    "TransportError",
    "Unauthenticated",
    "Forbidden",
    "AccountAdminRequired",
    "NotFound",
    "VersionConflict",
    "PreconditionRequired",
    "ValidationFailed",
    "MergeConflict",
    "EnvironmentMoved",
    "SnapshotNotReady",
    "PlanLimit",
    "Branch",
    "Conflict",
    "ContentType",
    "DeliveryKey",
    "Diff",
    "Entry",
    "Environment",
    "Invitation",
    "IssuedDeliveryKey",
    "IssuedInvitation",
    "Me",
    "Member",
    "MergeResult",
    "ProjectInfo",
    "ProjectRole",
    "RefInfo",
    "Resolution",
    "Tag",
    "Tier",
]
