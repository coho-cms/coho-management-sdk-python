"""Errors raised by the SDK.

Every failure the server reports is a `CohoError` carrying the problem document's
stable ``code``. **Branch on ``code``, never on status** — domain failures are all
``400`` with a distinct code, and only failures where an HTTP mechanism depends on
the status (401/403/404/412/428) keep a specific one.

Subclasses exist for the codes a caller is likely to branch on. Everything else
arrives as a plain ``CohoError`` whose ``.code`` is still exact.
"""

from __future__ import annotations

from typing import Any

import httpx

# Context fields the authoring tier attaches to problems (authoring.yaml `Problem`).
_CONTEXT_FIELDS = (
    "errors",
    "ref",
    "slug",
    "permission",
    "header",
    "depth",
    "maxDepth",
    "entryCount",
    "expectedVersion",
    "currentVersion",
    "conflicts",
    "limit",
    "current",
    "state",
)


class CohoError(Exception):
    """A refusal from Coho, or a failure to reach it.

    Attributes:
        code:    the stable token to branch on, e.g. ``VERSION_CONFLICT``.
        status:  the HTTP status, for logging. Do not branch on it.
        title:   the problem's short title.
        detail:  the human-readable detail, possibly empty.
        context: the problem's extra fields (``ref``, ``slug``, ``conflicts`` …).
        problem: the raw problem document, if there was one.
    """

    code: str = "UNKNOWN"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int = 0,
        title: str = "",
        detail: str = "",
        context: dict[str, Any] | None = None,
        problem: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.status = status
        self.title = title
        self.detail = detail
        self.context = context or {}
        self.problem = problem

    def __str__(self) -> str:
        base = super().__str__()
        return f"{self.code}: {base}" if base and not base.startswith(self.code) else base

    @classmethod
    def from_problem(cls, status: int, problem: dict[str, Any]) -> CohoError:
        """Build the most specific error class for a problem document."""
        code = str(problem.get("code") or f"HTTP_{status}")
        title = str(problem.get("title") or "")
        detail = str(problem.get("detail") or "")
        context = {k: problem[k] for k in _CONTEXT_FIELDS if k in problem}
        klass = _BY_CODE.get(code, CohoError)
        message = detail or title or code
        return klass(
            message,
            code=code,
            status=status,
            title=title,
            detail=detail,
            context=context,
            problem=problem,
        )


class TransportError(CohoError):
    """The server could not be reached, or answered with something that is not a problem."""

    code = "TRANSPORT"


class AuthError(CohoError):
    """The login flow itself failed (before any Coho request)."""

    code = "AUTH"


class NotLoggedIn(AuthError):
    """No token is available for the profile. Run ``coho login``."""

    code = "NOT_LOGGED_IN"


class Unauthenticated(CohoError):
    code = "UNAUTHENTICATED"


class Forbidden(CohoError):
    """The actor lacks a permission; ``context["permission"]`` names it."""

    code = "FORBIDDEN"

    @property
    def permission(self) -> str | None:
        return self.context.get("permission")


class NotFound(CohoError):
    code = "NOT_FOUND"


class RefNotFound(NotFound):
    code = "REF_NOT_FOUND"

    @property
    def ref(self) -> str | None:
        return self.context.get("ref")


class VersionConflict(CohoError):
    """``If-Match`` did not match the current version (412)."""

    code = "VERSION_CONFLICT"

    @property
    def expected_version(self) -> str | None:
        return self.context.get("expectedVersion")

    @property
    def current_version(self) -> str | None:
        return self.context.get("currentVersion")


class PreconditionRequired(CohoError):
    """An update or delete was sent without ``If-Match`` (428)."""

    code = "PRECONDITION_REQUIRED"


class ValidationFailed(CohoError):
    """The document does not match its content type; ``errors`` lists the paths."""

    code = "VALIDATION_FAILED"

    @property
    def errors(self) -> list[dict[str, Any]]:
        return list(self.context.get("errors") or [])


class SchemaInvalid(CohoError):
    code = "SCHEMA_INVALID"


class DestructiveSchemaChange(ValidationFailed):
    """Refused without ``confirmDestructive``; ``errors`` lists one entry per change."""

    code = "DESTRUCTIVE_SCHEMA_CHANGE"


class SlugConflict(CohoError):
    code = "SLUG_CONFLICT"


class TypeInUse(CohoError):
    code = "TYPE_IN_USE"


class RefNameTaken(CohoError):
    code = "REF_NAME_TAKEN"


class MergeConflict(CohoError):
    """The merge needs human decisions; ``conflicts`` carries them."""

    code = "MERGE_CONFLICT"

    @property
    def conflicts(self) -> list[dict[str, Any]]:
        return list(self.context.get("conflicts") or [])


class MergeRefsMoved(CohoError):
    code = "MERGE_REFS_MOVED"


class CannotMerge(CohoError):
    code = "CANNOT_MERGE"


class EnvironmentMoved(CohoError):
    """``expectedTarget`` did not match — somebody else repointed the environment."""

    code = "ENVIRONMENT_MOVED"


class SnapshotNotReady(CohoError):
    """The tag's snapshot is still being built. Retry, or let the SDK wait."""

    code = "SNAPSHOT_NOT_READY"


class PlanLimit(CohoError):
    """The account's plan refuses this write; ``limit`` and ``current`` say why."""

    code = "PLAN_LIMIT"


class AccountMismatch(CohoError):
    code = "ACCOUNT_MISMATCH"


class UpstreamUnavailable(TransportError):
    code = "UPSTREAM_UNAVAILABLE"


class Internal(CohoError):
    code = "INTERNAL"


# -- codes from the auth tier (accounts, members, invitations) ------------------
# That tier's problems are a bare ``{code, detail}``; the codes are still exact.


class InsufficientScope(Forbidden):
    """The token was issued without the scope this call needs (``coho-auth/accounts``)."""

    code = "INSUFFICIENT_SCOPE"


class AccountAdminRequired(Forbidden):
    """The caller is a member of the account but not one of its admins."""

    code = "ACCOUNT_ADMIN_REQUIRED"


class Invalid(CohoError):
    """The auth tier's validation failure: a bad role, a blank name, a non-UUID id …"""

    code = "INVALID"


class LastAdmin(CohoError):
    """Refused: it would leave the account with members but no admin."""

    code = "LAST_ADMIN"


class LastLogin(CohoError):
    code = "LAST_LOGIN"


class AlreadyRegistered(CohoError):
    code = "ALREADY_REGISTERED"


class AccountClosed(CohoError):
    """The account is closed; no membership change is possible."""

    code = "ACCOUNT_CLOSED"


class InvitationUnusable(CohoError):
    """The invitation was already used, revoked, or has expired. ``code`` says which."""

    code = "INVITATION_UNUSABLE"


_BY_CODE: dict[str, type[CohoError]] = {
    k.code: k
    for k in (
        Unauthenticated,
        Forbidden,
        NotFound,
        RefNotFound,
        VersionConflict,
        PreconditionRequired,
        ValidationFailed,
        SchemaInvalid,
        DestructiveSchemaChange,
        SlugConflict,
        TypeInUse,
        RefNameTaken,
        MergeConflict,
        MergeRefsMoved,
        CannotMerge,
        EnvironmentMoved,
        SnapshotNotReady,
        PlanLimit,
        AccountMismatch,
        UpstreamUnavailable,
        Internal,
        InsufficientScope,
        AccountAdminRequired,
        Invalid,
        LastAdmin,
        LastLogin,
        AlreadyRegistered,
        AccountClosed,
    )
}
for _code in ("INVITATION_USED", "INVITATION_ACCEPTED", "INVITATION_REVOKED", "INVITATION_EXPIRED"):
    _BY_CODE[_code] = InvitationUnusable
# Codes that share a class with a sibling.
_BY_CODE["ENTRY_NOT_FOUND"] = NotFound
_BY_CODE["ROUTE_NOT_FOUND"] = NotFound
_BY_CODE["USER_SUSPENDED"] = Forbidden
_BY_CODE["ACCOUNT_SUSPENDED"] = Forbidden


def error_for_response(response: httpx.Response) -> CohoError:
    """Turn a non-2xx response into the right `CohoError`.

    Problem documents (``application/problem+json``) are mapped by ``code``.
    Anything else — an HTML page from a proxy, an empty body — becomes a
    ``TransportError`` with code ``HTTP_<status>`` so the caller still gets a
    code to log.
    """
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            problem = response.json()
        except ValueError:
            problem = None
        if isinstance(problem, dict) and "code" in problem:
            return CohoError.from_problem(response.status_code, problem)
    text = response.text.strip()
    snippet = text[:200] if text else "(empty body)"
    return TransportError(
        f"{response.request.method} {response.request.url.path} answered {response.status_code} "
        f"without a problem document: {snippet}",
        code=f"HTTP_{response.status_code}",
        status=response.status_code,
    )
