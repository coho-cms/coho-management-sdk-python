# Errors

Every failure raises `coho_sdk.CohoError`:

| Attribute | |
|---|---|
| `code` | the stable token: `VERSION_CONFLICT`, `PLAN_LIMIT`, … Branch on this |
| `status` | the HTTP status. For logs; do not branch on it |
| `title`, `detail` | the problem's text |
| `context` | the problem's extra fields: `ref`, `slug`, `permission`, `errors`, `conflicts`, `limit`, `current`, `expectedVersion`, `currentVersion`, `state` |
| `problem` | the raw document |

```python
from coho_sdk import CohoError, VersionConflict, PlanLimit

try:
    dev.entries.put(post)
except VersionConflict as exc:
    print(exc.expected_version, exc.current_version)
except PlanLimit as exc:
    print(exc.context["limit"], exc.context["current"])
except CohoError as exc:
    if exc.code == "BRANCH_NOT_WRITABLE":
        ...
    raise
```

## Subclasses

Catch a subclass when you would branch on that code; everything else arrives as a
plain `CohoError` whose `.code` is still exact. Parent classes catch their children.

| Class | `code` | Extra |
|---|---|---|
| `TransportError` | `TRANSPORT`, `HTTP_<status>` | could not reach the server, or a non-problem response |
| ↳ `UpstreamUnavailable` | `UPSTREAM_UNAVAILABLE` | the BFF could not reach a tier |
| `AuthError` | `AUTH` | the login flow failed |
| ↳ `NotLoggedIn` | `NOT_LOGGED_IN` | no token for the profile |
| `Unauthenticated` | `UNAUTHENTICATED` | |
| `Forbidden` | `FORBIDDEN`, `USER_SUSPENDED`, `ACCOUNT_SUSPENDED` | `.permission` |
| ↳ `InsufficientScope` | `INSUFFICIENT_SCOPE` | token lacks a scope |
| ↳ `AccountAdminRequired` | `ACCOUNT_ADMIN_REQUIRED` | |
| `NotFound` | `NOT_FOUND`, `ENTRY_NOT_FOUND`, `ROUTE_NOT_FOUND` | |
| ↳ `RefNotFound` | `REF_NOT_FOUND` | `.ref` |
| `VersionConflict` | `VERSION_CONFLICT` | `.expected_version`, `.current_version` |
| `PreconditionRequired` | `PRECONDITION_REQUIRED` | also raised locally by `put`/`delete` without an ETag |
| `ValidationFailed` | `VALIDATION_FAILED` | `.errors` |
| ↳ `DestructiveSchemaChange` | `DESTRUCTIVE_SCHEMA_CHANGE` | `.errors` |
| `SchemaInvalid` | `SCHEMA_INVALID` | |
| `SlugConflict` | `SLUG_CONFLICT` | `context["slug"]` |
| `TypeInUse` | `TYPE_IN_USE` | `context["entryCount"]` |
| `RefNameTaken` | `REF_NAME_TAKEN` | |
| `MergeConflict` | `MERGE_CONFLICT` | `.conflicts` |
| `MergeRefsMoved` | `MERGE_REFS_MOVED` | |
| `CannotMerge` | `CANNOT_MERGE` | |
| `EnvironmentMoved` | `ENVIRONMENT_MOVED` | |
| `SnapshotNotReady` | `SNAPSHOT_NOT_READY` | `context["state"]` |
| `PlanLimit` | `PLAN_LIMIT` | 400 from authoring, 409 from auth — same class |
| `AccountMismatch` | `ACCOUNT_MISMATCH` | |
| `Invalid` | `INVALID` | auth tier validation |
| `LastAdmin` | `LAST_ADMIN` | |
| `LastLogin` | `LAST_LOGIN` | |
| `AlreadyRegistered` | `ALREADY_REGISTERED` | |
| `AccountClosed` | `ACCOUNT_CLOSED` | |
| `InvitationUnusable` | `INVITATION_USED`, `INVITATION_ACCEPTED`, `INVITATION_REVOKED`, `INVITATION_EXPIRED` | |
| `Internal` | `INTERNAL` | opaque by design |

What each code means in the product is in the [error catalogue](https://github.com/coho-cms/coho-cli/blob/main/docs/errors.md).

## Two problem shapes, one interface

Authoring and the BFF answer RFC 9457 documents (`application/problem+json`, with
`type`/`title`/`status`); the auth tier answers a bare `{code, detail}` with
`application/json`, relayed untouched. `error_for_response` maps both by `code`, so
you never see the difference. A response with no `code` at all (a proxy's HTML page)
becomes `TransportError` with code `HTTP_<status>`.
