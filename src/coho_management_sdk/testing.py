"""A fake BFF on a real port, shaped like the contracts — for tests, yours included.

Install ``coho-management-sdk[testing]`` (pytest-httpserver) and use `FakeBff` with the
``httpserver`` fixture::

    from coho_management_sdk.testing import FakeBff, ACCOUNT, PROJECT

    def test_something(httpserver):
        bff = FakeBff(httpserver)
        coho = Coho(bff.url, token="test-token")
        ...

Nothing here needs the real tiers. The fake is deliberately literal: it answers with
the exact JSON shapes in bff.yaml / authoring.yaml and the auth tier's ``{code,
detail}`` problems, so a test reads like a transcript against the real thing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from typing import Any

from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

ACCOUNT = "11111111-1111-4111-8111-111111111111"
PROJECT = "22222222-2222-4222-8222-222222222222"
# An account that no longer exists, as one remembered from before a reset is: everything
# under it answers ACCOUNT_NOT_FOUND, as the BFF does for an account the caller is not in.
GONE_ACCOUNT = "33333333-3333-4333-8333-333333333333"
ENTRY = "33333333-3333-4333-8333-333333333333"
ACTOR = "44444444-4444-4444-8444-444444444444"
USER = "55555555-5555-4555-8555-555555555555"

# The one invitation token the fake recognizes.
INVITATION_TOKEN = "invite-token-abc"

# How many invalid entries a TAG_CONTENT_INVALID lists, as the server caps it.
# `entryCount` has the total.
INVALID_ENTRIES_SHOWN = 100

ME = {
    "user": {"id": USER, "displayName": "Ada", "email": "ada@acme.example"},
    "accounts": [{"accountId": ACCOUNT, "accountName": "Acme", "role": "admin", "actorId": ACTOR}],
}

# What GET .../projects lists. Module-level so a test can change it.
PROJECTS = [{"id": PROJECT, "name": "Marketing site", "role": "owner"}]

PROJECT_DOC = {
    "id": PROJECT,
    "name": "Marketing site",
    "trunk": "v0.0.x",
    "environments": [
        {"name": "dev", "target": "v0.0.x"},
        {"name": "qa", "target": None},
        {"name": "stage", "target": None},
        {"name": "prod", "target": None},
    ],
}


def problem(status: int, code: str, detail: str = "", **context: Any) -> Response:
    """An authoring/BFF-style RFC 9457 problem."""
    body = {
        "code": code,
        "type": f"urn:coho:{code}",
        "title": code,
        "status": status,
        "detail": detail,
    }
    body.update(context)
    return Response(json.dumps(body), status=status, content_type="application/problem+json")


def auth_problem(status: int, code: str, detail: str = "") -> Response:
    """The auth tier's bare ``{code, detail}`` shape, relayed untouched by the BFF."""
    return Response(
        json.dumps({"code": code, "detail": detail}), status=status, content_type="application/json"
    )


def ok(body: Any, status: int = 200, **headers: str) -> Response:
    return Response(
        json.dumps(body), status=status, content_type="application/json", headers=headers
    )


class FakeBff:
    """Registers routes on a pytest-httpserver and records what it saw."""

    def __init__(self, server: HTTPServer) -> None:
        self.server = server
        self.requests: list[Request] = []
        # The bearer tokens `/me` answers for. Add one to stand for an identity that has
        # just finished setting up in a browser.
        self.tokens: set[str] = {"test-token"}
        self.snapshot_not_ready_times = 0
        # What a tag cut with `validate` finds wrong at the tag point, each shaped like an
        # item of the problem's `invalidEntries`. Empty means the content is valid.
        self.invalid_entries: list[dict[str, Any]] = []
        self.entry_version = "v1"
        self.entry_fields: dict[str, Any] = {"title": {"en-US": "Hello"}, "rank": 1}
        # Assets (doc 40). `blobs` is what the fake object store holds, by sha256;
        # `uploads` what was begun; `assets` what was completed, by id.
        self.blobs: dict[str, bytes] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        # Whether the caller is an admin of ACCOUNT, as the account's usage report asks.
        # False stands for a member, refused as authoring refuses one.
        self.account_admin = True
        self._install()
        self._install_assets()
        self._install_usage()

    @property
    def url(self) -> str:
        return self.server.url_for("").rstrip("/")

    def _record(self, request: Request) -> None:
        self.requests.append(request)

    def last(self) -> Request:
        return self.requests[-1]

    def _install(self) -> None:
        s = self.server
        p = f"/api/v1/accounts/{ACCOUNT}/projects/{PROJECT}"

        def me(request: Request) -> Response:
            self._record(request)
            if request.headers.get("Authorization", "").removeprefix("Bearer ") not in self.tokens:
                return problem(401, "UNAUTHENTICATED", "no credential")
            return ok(ME)

        s.expect_request("/api/v1/me", method="GET").respond_with_handler(me)
        s.expect_request(
            re.compile(rf"^/api/v1/accounts/{GONE_ACCOUNT}(/.*)?$")
        ).respond_with_handler(lambda request: problem(404, "ACCOUNT_NOT_FOUND", "No such account"))

        # --- account management (auth tier shapes) ---
        s.expect_request(f"/api/v1/accounts/{ACCOUNT}/users", method="GET").respond_with_json(
            {
                "users": [
                    {"id": USER, "displayName": "Ada", "email": "ada@acme.example", "role": "admin"}
                ]
            }
        )
        s.expect_request(
            f"/api/v1/accounts/{ACCOUNT}/entitlements", method="GET"
        ).respond_with_json(
            {"plan": "free@2", "planStatus": "active", "maxProjects": 5, "maxMembers": 5}
        )

        def invite(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body.get("role") not in ("admin", "member"):
                return auth_problem(400, "INVALID", "bad role")
            return ok(
                {
                    "invitation": {
                        "id": "inv-1",
                        "accountId": ACCOUNT,
                        "accountName": "Acme",
                        "email": body["email"],
                        "role": body["role"],
                        "status": "pending",
                        "createdAt": "2026-09-22T00:00:00Z",
                        "expiresAt": "2026-09-29T00:00:00Z",
                    },
                    "token": "secret-invitation-token",
                },
                201,
            )

        s.expect_request(
            f"/api/v1/accounts/{ACCOUNT}/invitations", method="POST"
        ).respond_with_handler(invite)

        def change_role(request: Request) -> Response:
            self._record(request)
            if request.get_json().get("role") == "member":
                return auth_problem(409, "LAST_ADMIN", "would leave no admin")
            return ok(
                {"id": USER, "displayName": "Ada", "email": "ada@acme.example", "role": "admin"}
            )

        s.expect_request(
            f"/api/v1/accounts/{ACCOUNT}/members/{USER}", method="PATCH"
        ).respond_with_handler(change_role)

        # --- projects ---
        # Note: werkzeug hands the fake *decoded* paths, so `feature%2Fpricing` is
        # registered as `feature/pricing`. The SDK still sends it encoded.
        def create_project(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            return ok(PROJECT_DOC | {"name": body["name"]}, 201, Location=f"{p}")

        s.expect_request(
            f"/api/v1/accounts/{ACCOUNT}/projects", method="POST"
        ).respond_with_handler(create_project)

        def list_projects(request: Request) -> Response:
            self._record(request)
            return ok({"projects": PROJECTS})

        s.expect_request(f"/api/v1/accounts/{ACCOUNT}/projects", method="GET").respond_with_handler(
            list_projects
        )
        s.expect_request(p, method="GET").respond_with_json(PROJECT_DOC)
        s.expect_request(f"{p}/refs", method="GET").respond_with_json(
            {
                "refs": [
                    {"name": "v0.0.x", "kind": "version_branch"},
                    {
                        "name": "feature/pricing",
                        "kind": "feature_branch",
                        "description": "New pricing",
                    },
                    {
                        "name": "v1.0.0",
                        "kind": "tag",
                        "target": "v0.0.x@42",
                        "description": "First release",
                    },
                    {"name": "dev", "kind": "environment", "target": "v0.0.x"},
                    {"name": "prod", "kind": "environment"},
                ]
            }
        )
        s.expect_request(f"{p}/refs/feature/pricing", method="GET").respond_with_json(
            {"name": "feature/pricing", "kind": "feature_branch", "description": "New pricing"}
        )
        s.expect_request(f"{p}/refs/dev", method="GET").respond_with_json(
            {"name": "dev", "kind": "environment", "target": "v0.0.x"}
        )
        s.expect_request(f"{p}/refs/nope", method="GET").respond_with_handler(
            lambda r: problem(404, "REF_NOT_FOUND", "no such ref", ref="nope")
        )

        def create_branch(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body["name"] == "taken":
                return problem(400, "REF_NAME_TAKEN", "already exists", ref="taken")
            return ok(
                {
                    "name": body["name"],
                    "kind": body.get("kind", "feature_branch"),
                    "from": body["from"],
                    "branchedAt": 42,
                    "depth": 1,
                },
                201,
            )

        s.expect_request(f"{p}/branches", method="POST").respond_with_handler(create_branch)

        def create_tag(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body.get("validate") and self.invalid_entries:
                count = len(self.invalid_entries)
                fail = "entry fails" if count == 1 else "entries fail"
                return problem(
                    400,
                    "TAG_CONTENT_INVALID",
                    f"{count} {fail} validation at the tag point. "
                    "Correct them on the branch and tag again; nothing was tagged.",
                    entryCount=count,
                    invalidEntries=self.invalid_entries[:INVALID_ENTRIES_SHOWN],
                )
            return ok(
                {
                    "name": body["name"],
                    "ref": body["ref"],
                    "at": body.get("at", 42),
                    "warnings": [
                        {"code": "PENDING_FORWARD_PORT", "message": "3 nodes not forward-ported"}
                    ],
                },
                201,
            )

        s.expect_request(f"{p}/tags", method="POST").respond_with_handler(create_tag)

        # --- diff & merge ---
        s.expect_request(f"{p}/diff", method="GET").respond_with_json(
            {
                "from": "v0.0.x",
                "to": "feature/pricing",
                "mode": "diverged",
                "base": {"ref": "v0.0.x", "seq": 40},
                "summary": {"added": 1, "modified": 1, "deleted": 0},
                "nodes": [
                    {
                        "nodeId": ENTRY,
                        "kind": "entry",
                        "slug": "pricing",
                        "change": "modified",
                        "fields": [
                            {
                                "path": "title.en-US",
                                "change": "modified",
                                "before": "Old",
                                "after": "New",
                            }
                        ],
                    }
                ],
                "conflicts": [
                    {
                        "nodeId": ENTRY,
                        "slug": "pricing",
                        "reason": "field",
                        "paths": ["title.en-US"],
                    }
                ],
                "mergeToken": "tok-123",
            }
        )

        def merge(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if not body.get("resolutions"):
                return problem(
                    400,
                    "MERGE_CONFLICT",
                    "needs decisions",
                    conflicts=[
                        {
                            "nodeId": ENTRY,
                            "slug": "pricing",
                            "reason": "field",
                            "paths": ["title.en-US"],
                        }
                    ],
                )
            return ok(
                {
                    "source": body["source"],
                    "target": body["target"],
                    "merged": 2,
                    "unchanged": 10,
                    "resolved": len(body["resolutions"]),
                    "base": {"ref": "v0.0.x", "seq": 40},
                }
            )

        s.expect_request(f"{p}/merges", method="POST").respond_with_handler(merge)

        # --- environments ---
        s.expect_request(f"{p}/environments", method="GET").respond_with_json(
            {
                "environments": [
                    {"name": "dev", "tier": "dev", "resolves": "live", "target": "v0.0.x"},
                    {"name": "prod", "tier": "prod", "resolves": "snapshot", "target": "v1.0.0"},
                ]
            }
        )

        def promote(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if self.snapshot_not_ready_times > 0:
                self.snapshot_not_ready_times -= 1
                return problem(400, "SNAPSHOT_NOT_READY", "building", state="BUILDING")
            if body.get("expectedTarget") == "wrong":
                return problem(400, "ENVIRONMENT_MOVED", "moved")
            return ok(
                {
                    "name": "prod",
                    "tier": "prod",
                    "target": body["target"],
                    "previousTarget": "v1.0.0",
                    "warnings": []
                    if body["target"] != "v9.0.0"
                    else [{"code": "SKIPPED_TIER", "message": "skipped stage"}],
                }
            )

        s.expect_request(f"{p}/environments/prod", method="PUT").respond_with_handler(promote)
        s.expect_request(f"{p}/environments/prod/history", method="GET").respond_with_json(
            {
                "ref": "prod",
                "hasMore": False,
                "history": [
                    {
                        "ref": "prod",
                        "from": "v0.9.0",
                        "to": "v1.0.0",
                        "reason": "release",
                        "actor": ACTOR,
                        "at": "2026-09-22T00:00:00Z",
                    },
                    {
                        "ref": "prod",
                        "from": None,
                        "to": "v0.9.0",
                        "reason": None,
                        "actor": ACTOR,
                        "at": "2026-09-21T00:00:00Z",
                    },
                ],
            }
        )

        # --- roles & keys ---
        s.expect_request(f"{p}/roles", method="GET").respond_with_json(
            {
                "roles": [
                    {
                        "actorId": ACTOR,
                        "role": "owner",
                        "grantedBy": ACTOR,
                        "grantedAt": "2026-09-22T00:00:00Z",
                    }
                ]
            }
        )

        def grant(request: Request) -> Response:
            self._record(request)
            return ok(
                {
                    "actorId": ACTOR,
                    "role": request.get_json()["role"],
                    "grantedBy": ACTOR,
                    "grantedAt": "now",
                }
            )

        s.expect_request(f"{p}/roles/{ACTOR}", method="PUT").respond_with_handler(grant)

        def issue_key(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body.get("refs") == []:
                return problem(400, "BAD_REQUEST", "empty refs")
            return ok(
                {
                    "key": "coho_dk_SECRET",
                    "delivery": {
                        "id": "key-1",
                        "label": body.get("label"),
                        "refs": body.get("refs"),
                        "createdAt": "now",
                    },
                },
                201,
            )

        s.expect_request(f"{p}/delivery-keys", method="POST").respond_with_handler(issue_key)

        # --- project tokens (doc 29) ---
        token_doc = {
            "id": "tok-1",
            "label": "CI",
            "role": "maintainer",
            "state": "active",
            "createdBy": ACTOR,
            "createdAt": "now",
            "expiresAt": "later",
        }

        def create_token(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body.get("role") not in ("viewer", "author", "maintainer", "release_manager"):
                return problem(400, "BAD_REQUEST", "role must be below owner")
            doc = {**token_doc, "label": body["label"], "role": body["role"]}
            return ok({"token": "coho_pt_SECRET", "projectToken": doc}, 201)

        def revoke_token(request: Request) -> Response:
            self._record(request)
            return Response(status=204)

        s.expect_request(f"{p}/tokens", method="POST").respond_with_handler(create_token)
        s.expect_request(f"{p}/tokens", method="GET").respond_with_json({"tokens": [token_doc]})
        s.expect_request(f"{p}/tokens/tok-1", method="DELETE").respond_with_handler(revoke_token)
        s.expect_request(f"{p}/delivery-keys", method="GET").respond_with_json(
            {"keys": [{"id": "key-1", "label": "site", "createdAt": "now"}], "publicRefs": ["prod"]}
        )

        # --- content on dev ---
        r = f"{p}/refs/dev"
        s.expect_request(f"{r}/types", method="GET").respond_with_json(
            {
                "types": [
                    {
                        "slug": "blogPost",
                        "version": "t1",
                        "definition": {
                            "_name": "Blog post",
                            "fields": [
                                {"id": "title", "type": "text", "localized": True, "required": True}
                            ],
                        },
                    }
                ]
            }
        )

        def get_type(request: Request) -> Response:
            self._record(request)
            return ok(
                {
                    "slug": "blogPost",
                    "version": "t1",
                    "definition": {"_name": "Blog post", "fields": []},
                },
                ETag='"t1"',
            )

        def put_type(request: Request) -> Response:
            self._record(request)
            if request.headers.get("If-Match") is None:
                return problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            if request.headers.get("If-Match") != '"t1"':
                return problem(
                    412, "VERSION_CONFLICT", "stale", expectedVersion="t0", currentVersion="t1"
                )
            body = request.get_json()
            return ok(
                {"slug": "blogPost", "version": "t2", "definition": body["definition"]}, ETag='"t2"'
            )

        # Traits: `seo`, which blogPost takes, and `unused`, which nothing does.
        seo = {
            "slug": "seo",
            "version": "s1",
            "definition": {
                "_name": "SEO",
                "fields": [{"id": "metaTitle", "type": "text", "max": 70}],
            },
        }
        s.expect_request(f"{r}/traits", method="GET").respond_with_json({"traits": [seo]})

        def get_trait(request: Request) -> Response:
            self._record(request)
            return ok(seo, ETag='"s1"')

        def put_trait(request: Request) -> Response:
            self._record(request)
            if request.headers.get("If-Match") != '"s1"':
                return problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            body = request.get_json()
            if not body.get("confirmDestructive") and any(
                f.get("required") for f in body["definition"].get("fields", [])
            ):
                return problem(
                    400,
                    "DESTRUCTIVE_SCHEMA_CHANGE",
                    "would invalidate entries",
                    errors=[{"path": "metaTitle", "message": "field 'metaTitle' became required"}],
                    affectedTypes=["blogPost"],
                    entryCount=5,
                )
            return ok(
                {"slug": "seo", "version": "s2", "definition": body["definition"]}, ETag='"s2"'
            )

        s.expect_request(f"{r}/traits/seo", method="GET").respond_with_handler(get_trait)
        s.expect_request(f"{r}/traits/seo", method="PUT").respond_with_handler(put_trait)
        s.expect_request(f"{r}/traits/seo", method="DELETE").respond_with_handler(
            lambda req: problem(
                400, "TRAIT_IN_USE", "Trait 'seo' is taken by blogPost", affectedTypes=["blogPost"]
            )
        )
        s.expect_request(f"{r}/traits/unused", method="GET").respond_with_handler(
            lambda req: ok(
                {
                    "slug": "unused",
                    "version": "u1",
                    "definition": {"_name": "Unused", "fields": []},
                },
                ETag='"u1"',
            )
        )
        s.expect_request(f"{r}/traits/unused", method="DELETE").respond_with_handler(
            lambda req: (
                Response(status=204)
                if req.headers.get("If-Match") == '"u1"'
                else problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            )
        )

        s.expect_request(f"{r}/types/blogPost", method="GET").respond_with_handler(get_type)
        s.expect_request(f"{r}/types/blogPost", method="PUT").respond_with_handler(put_type)
        s.expect_request(f"{r}/types/newType", method="GET").respond_with_handler(
            lambda req: problem(404, "NOT_FOUND", "no such type")
        )
        s.expect_request(f"{r}/types/newType", method="PUT").respond_with_handler(
            lambda req: ok(
                {"slug": "newType", "version": "n1", "definition": req.get_json()["definition"]},
                201,
                ETag='"n1"',
            )
        )

        def list_entries(request: Request) -> Response:
            self._record(request)
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 100))
            if limit > 1000:
                return problem(400, "BAD_REQUEST", "limit out of range")
            all_entries = [self._entry(i) for i in range(5)]
            page = all_entries[offset : offset + limit]
            return ok(
                {
                    "entries": page,
                    "total": len(page),
                    "page": {
                        "limit": limit,
                        "offset": offset,
                        "hasMore": offset + limit < len(all_entries),
                    },
                }
            )

        s.expect_request(f"{r}/entries", method="GET").respond_with_handler(list_entries)

        def create_entry(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            if body["slug"] == "taken":
                return problem(400, "SLUG_CONFLICT", "slug in use", slug="taken", ref="dev")
            if "title" not in body["fields"]:
                return problem(
                    400,
                    "VALIDATION_FAILED",
                    "does not match type",
                    errors=[{"path": "title", "message": "required"}],
                )
            return ok(
                {
                    "id": ENTRY,
                    "type": body["type"],
                    "slug": body["slug"],
                    "version": "v1",
                    "fields": body["fields"],
                },
                201,
                ETag='"v1"',
            )

        s.expect_request(f"{r}/entries", method="POST").respond_with_handler(create_entry)

        def get_entry(request: Request) -> Response:
            self._record(request)
            return ok(
                {
                    "id": ENTRY,
                    "type": "blogPost",
                    "slug": "hello",
                    "version": self.entry_version,
                    "fields": self.entry_fields,
                },
                ETag=f'"{self.entry_version}"',
            )

        def put_entry(request: Request) -> Response:
            self._record(request)
            etag = request.headers.get("If-Match")
            if etag is None:
                return problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            if etag != f'"{self.entry_version}"':
                return problem(
                    412,
                    "VERSION_CONFLICT",
                    "stale",
                    expectedVersion=etag.strip('"'),
                    currentVersion=self.entry_version,
                )
            self.entry_fields = request.get_json()["fields"]
            self.entry_version = "v" + str(int(self.entry_version[1:]) + 1)
            return ok(
                {
                    "id": ENTRY,
                    "type": "blogPost",
                    "slug": "hello",
                    "version": self.entry_version,
                    "fields": self.entry_fields,
                },
                ETag=f'"{self.entry_version}"',
            )

        def delete_entry(request: Request) -> Response:
            self._record(request)
            if request.headers.get("If-Match") is None:
                return problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            return Response("", status=204, headers={"ETag": '"tombstone"'})

        s.expect_request(f"{r}/entries/{ENTRY}", method="GET").respond_with_handler(get_entry)
        s.expect_request(f"{r}/entries/{ENTRY}", method="PUT").respond_with_handler(put_entry)
        s.expect_request(f"{r}/entries/{ENTRY}", method="DELETE").respond_with_handler(delete_entry)
        s.expect_request(f"{r}/entries/{ENTRY}/references", method="GET").respond_with_json(
            {"id": ENTRY, "ref": "dev", "incoming": [], "total": 0}
        )
        s.expect_request(f"{r}/entries/{ENTRY}/history", method="GET").respond_with_json(
            {
                "id": ENTRY,
                "ref": "dev",
                "versions": [
                    {
                        "version": "v2",
                        "parent": "v1",
                        "seq": 43,
                        "op": "upsert",
                        "actor": ACTOR,
                        "at": "now",
                        "branch": "dev",
                    },
                    {
                        "version": "v1",
                        "parent": None,
                        "seq": 42,
                        "op": "upsert",
                        "actor": ACTOR,
                        "at": "then",
                        "branch": "dev",
                    },
                ],
                "page": {"limit": 50, "offset": 0, "hasMore": False},
            }
        )

        # --- export ---
        def export_branch(request: Request) -> Response:
            self._record(request)
            lines = "\n".join(json.dumps(self._entry(i)) for i in range(3)) + "\n"
            return Response(
                lines,
                status=200,
                content_type="application/x-ndjson",
                headers={"X-Coho-Export-Seq": "42"},
            )

        s.expect_request(f"{p}/refs/feature/pricing/export", method="GET").respond_with_handler(
            export_branch
        )
        s.expect_request(f"{p}/refs/v1.0.0/export", method="GET").respond_with_handler(
            lambda req: Response(
                "", status=302, headers={"Location": f"{self.url}/blob/v1.0.0.ndjson"}
            )
        )
        s.expect_request("/blob/v1.0.0.ndjson", method="GET").respond_with_data(
            b'{"id":"a"}\n', content_type="application/x-ndjson"
        )

        # --- preview: delivery's EntryGraph, not authoring's {items, page} ---
        s.expect_request(f"{p}/preview/dev/entries", method="GET").respond_with_json(
            {
                "roots": [ENTRY],
                "entries": {
                    ENTRY: {
                        "id": ENTRY,
                        "type": "blogPost",
                        "slug": "hello",
                        "fields": {"title": "Hello"},
                    }
                },
                "unresolved": {},
                "hasMore": False,
            }
        )

        # --- before a login: invitations. The token in the body is the credential ---
        def invitation_lookup(request: Request) -> Response:
            self._record(request)
            if request.get_json().get("invitation") != INVITATION_TOKEN:
                return auth_problem(404, "NOT_FOUND", "no such invitation")
            return ok(
                {
                    "id": "inv-1",
                    "accountId": ACCOUNT,
                    "accountName": "Acme",
                    "email": "jane@acme.example",
                    "role": "member",
                    "status": "pending",
                    "createdAt": "2026-09-22T00:00:00Z",
                    "expiresAt": "2026-09-29T00:00:00Z",
                }
            )

        s.expect_request("/auth/invitations/lookup", method="POST").respond_with_handler(
            invitation_lookup
        )

        def invitation_accept(request: Request) -> Response:
            self._record(request)
            return ok({"location": "https://idp.example/oauth2/authorize?state=accept"})

        s.expect_request("/auth/invitations/accept", method="POST").respond_with_handler(
            invitation_accept
        )

        # --- a non-problem failure, as a proxy would produce ---
        s.expect_request("/api/v1/accounts/gateway/projects", method="POST").respond_with_data(
            "<html>502 Bad Gateway</html>", status=502, content_type="text/html"
        )

    def _install_assets(self) -> None:
        """The asset routes on ``dev``, and a fake object store for the presigned PUT.

        The store checks what S3 checks: the body's SHA-256 against the signed
        ``x-amz-checksum-sha256``, and that the staging tag was sent. A refusal is S3's
        XML, not a problem document.
        """
        s = self.server
        r = f"/api/v1/accounts/{ACCOUNT}/projects/{PROJECT}/refs/dev"
        uid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

        def begin(request: Request) -> Response:
            self._record(request)
            body = request.get_json()
            sha = str(body.get("sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                return problem(400, "BAD_REQUEST", "sha256 must be 64 lower-case hex digits")
            if int(body["byteSize"]) > 1 << 30:
                return problem(
                    400, "ASSET_TOO_LARGE", "too large", limit=1 << 30, current=body["byteSize"]
                )
            upload_id = str(uuid.uuid4())
            self.uploads[upload_id] = {**body, "assetId": None}
            doc: dict[str, Any] = {"id": upload_id, "expiresAt": "2026-10-18T00:00:00Z"}
            if sha not in self.blobs:
                checksum = base64.b64encode(bytes.fromhex(sha)).decode()
                headers = {
                    "x-amz-checksum-sha256": checksum,
                    "x-amz-tagging": "coho-lifecycle=staging",
                }
                if body.get("contentType"):
                    headers["content-type"] = body["contentType"]
                doc["upload"] = {
                    "url": f"{self.url}/fake-s3/{upload_id}?X-Amz-Signature=secret",
                    "method": "PUT",
                    "headers": headers,
                    "expiresAt": "2026-10-18T00:00:00Z",
                }
            return ok(doc, 201)

        def store(request: Request) -> Response:
            self._record(request)
            upload = self.uploads.get(request.path.rsplit("/", 1)[-1])
            data = request.get_data()
            if upload is None or "x-amz-tagging" not in request.headers:
                return _s3_error(403, "SignatureDoesNotMatch", "a signed header is missing")
            digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
            if digest != request.headers.get("x-amz-checksum-sha256"):
                return _s3_error(400, "BadDigest", "the content does not match the checksum")
            self.blobs[upload["sha256"]] = data
            return Response("", status=200, headers={"ETag": '"s3"'})

        def complete(request: Request) -> Response:
            self._record(request)
            upload_id = request.path.split("/")[-2]
            upload = self.uploads.get(upload_id)
            if upload is None:
                return problem(404, "NOT_FOUND", "Upload not found")
            if upload["assetId"] is not None:
                existing = self.assets[upload["assetId"]]
                return ok(existing, 200, ETag=f'"{existing["version"]}"')
            data = self.blobs.get(upload["sha256"])
            if data is None:
                return problem(400, "ASSET_UPLOAD_INCOMPLETE", "nothing uploaded yet")
            sniffed = _sniff(data)
            if sniffed is None:
                return problem(400, "ASSET_TYPE_NOT_ALLOWED", "not a type Coho stores")
            body = request.get_json()
            slug = body["slug"]
            if any(a["slug"] == slug for a in self.assets.values()) or slug == "taken":
                return problem(400, "SLUG_CONFLICT", "slug in use", slug=slug, ref="dev")
            asset_id = str(uuid.uuid4())
            file = {
                "sha256": upload["sha256"],
                "byteSize": len(data),
                "mediaType": sniffed[0],
                "filename": upload["filename"],
            }
            if sniffed[1] is not None:
                file["width"], file["height"] = sniffed[1], sniffed[2]
            asset = self._asset(asset_id, slug, "a1", file, body)
            self.assets[asset_id] = asset
            upload["assetId"] = asset_id
            return ok(asset, 201, ETag='"a1"', Location=f"{r}/assets/{asset_id}")

        def list_assets(request: Request) -> Response:
            self._record(request)
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 100))
            ordered = sorted(self.assets.values(), key=lambda a: a["slug"])
            page = ordered[offset : offset + limit]
            return ok(
                {
                    "assets": page,
                    "page": {
                        "limit": limit,
                        "offset": offset,
                        "hasMore": offset + limit < len(ordered),
                    },
                }
            )

        def one(request: Request) -> Response:
            self._record(request)
            asset_id = request.path.rsplit("/", 1)[-1]
            asset = self.assets.get(asset_id)
            if asset is None:
                return problem(404, "NOT_FOUND", f"Asset '{asset_id}' not found")
            if request.method == "GET":
                return ok(asset, ETag=f'"{asset["version"]}"')
            etag = request.headers.get("If-Match")
            if etag is None:
                return problem(428, "PRECONDITION_REQUIRED", "If-Match required", header="If-Match")
            if etag != f'"{asset["version"]}"':
                return problem(
                    412,
                    "VERSION_CONFLICT",
                    "stale",
                    expectedVersion=etag.strip('"'),
                    currentVersion=asset["version"],
                )
            if request.method == "DELETE":
                del self.assets[asset_id]
                return Response("", status=204, headers={"ETag": '"tombstone"'})
            version = "a" + str(int(asset["version"][1:]) + 1)
            edited = self._asset(
                asset_id, asset["slug"], version, asset["file"], request.get_json()
            )
            self.assets[asset_id] = edited
            return ok(edited, ETag=f'"{version}"')

        s.expect_request(f"{r}/assets/uploads", method="POST").respond_with_handler(begin)
        s.expect_request(re.compile(rf"^/fake-s3/{uid}$"), method="PUT").respond_with_handler(store)
        s.expect_request(
            re.compile(rf"^{re.escape(r)}/assets/uploads/{uid}/complete$"), method="POST"
        ).respond_with_handler(complete)
        s.expect_request(f"{r}/assets", method="GET").respond_with_handler(list_assets)
        s.expect_request(re.compile(rf"^{re.escape(r)}/assets/{uid}$")).respond_with_handler(one)

    def _install_usage(self) -> None:
        """Usage reports (doc 41 §7.1): `USAGE` for the project, summed for the account."""
        s = self.server
        a = f"/api/v1/accounts/{ACCOUNT}"

        def query_problem(request: Request) -> Response | None:
            if request.args.get("grain", "hour") not in ("hour", "day"):
                return problem(400, "USAGE_QUERY_INVALID", "grain must be hour or day")
            return None

        def report_for(request: Request) -> dict[str, Any]:
            report = usage_report(request.args.get("grain", "hour"))
            if request.args.get("meter"):
                names = request.args["meter"].split(",")
                report["meters"] = [m for m in report["meters"] if m["meter"] in names]
            return report

        def project_usage(request: Request) -> Response:
            self._record(request)
            return query_problem(request) or ok(report_for(request))

        def account_usage(request: Request) -> Response:
            self._record(request)
            if not self.account_admin:
                return problem(
                    403,
                    "FORBIDDEN",
                    "This action requires the 'account:admin' permission",
                    permission="account:admin",
                )
            bad = query_problem(request)
            if bad:
                return bad
            report = report_for(request)
            totals = {m["meter"]: _total(m) for m in report["meters"]}
            for m in report["meters"]:
                values = [sum(col) for col in zip(*(x["values"] for x in m["series"]), strict=True)]
                m["series"] = [{"dims": {}, "values": values, "total": _total(m, values)}]
            report["projects"] = [{"id": PROJECT, "name": "Marketing site", "totals": totals}]
            return ok(report)

        s.expect_request(f"{a}/projects/usage", method="GET").respond_with_handler(account_usage)
        s.expect_request(f"{a}/projects/{PROJECT}/usage", method="GET").respond_with_handler(
            project_usage
        )

    def _asset(
        self, asset_id: str, slug: str, version: str, file: dict[str, Any], details: dict[str, Any]
    ) -> dict[str, Any]:
        base = f"https://assets.example.test/{PROJECT}"
        name = str(file["filename"]).replace(" ", "%20")
        doc: dict[str, Any] = {
            "id": asset_id,
            "slug": slug,
            "version": version,
            "file": file,
            "tags": list(details.get("tags") or []),
            "url": f"{base}/a/{file['sha256']}/{name}",
        }
        for key in ("title", "alt", "focalPoint"):
            if details.get(key) is not None:
                doc[key] = details[key]
        if file["mediaType"] in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            doc["imageUrl"] = f"{base}/i/{file['sha256']}/{name}"
        return doc

    def _entry(self, i: int) -> dict[str, Any]:
        return {
            "id": f"0000000{i}-0000-4000-8000-000000000000",
            "type": "blogPost",
            "slug": f"post-{i}",
            "version": f"e{i}",
            "fields": {"title": {"en-US": f"Post {i}"}, "rank": i},
        }


# A project's usage over three periods, in authoring's shape: delivery by key and outcome,
# management by actor kind and class, and the stocks. key-1's client asks for each URL
# eleven times a period (330 answered over 30 distinct URLs); keyless reads of prod, once.
USAGE_METERS: list[tuple[str, str, str, list[tuple[dict[str, str], list[int]]]]] = [
    (
        "delivery.requests",
        "flow",
        "requests",
        [
            ({"key": "key-1", "access": "key", "outcome": "ok"}, [100, 120, 80]),
            ({"key": "key-1", "access": "key", "outcome": "not_modified"}, [10, 10, 10]),
            ({"key": "none", "access": "public", "outcome": "ok"}, [5, 0, 5]),
            ({"key": "key-2", "access": "key", "outcome": "refused"}, [0, 2, 0]),
        ],
    ),
    (
        "delivery.bytes",
        "flow",
        "bytes",
        [
            ({"key": "key-1", "access": "key"}, [400_000, 480_000, 320_000]),
            ({"key": "none", "access": "public"}, [20_000, 0, 20_000]),
        ],
    ),
    ("delivery.billable_bytes", "flow", "bytes", [({}, [1_638_400, 1_966_080, 1_310_720])]),
    (
        "delivery.distinct_urls",
        "distinct",
        "urls",
        [
            ({"key": "key-1"}, [10, 12, 8]),
            ({"key": "none"}, [5, 0, 5]),
        ],
    ),
    ("delivery.peak_rpm", "peak", "requests/minute", [({}, [40, 55, 30])]),
    (
        "management.requests",
        "flow",
        "requests",
        [
            ({"actor": "person", "class": "read", "outcome": "ok"}, [3, 4, 5]),
            ({"actor": "person", "class": "write", "outcome": "ok"}, [1, 0, 1]),
            ({"actor": "token", "class": "read", "outcome": "ok"}, [20, 20, 20]),
            ({"actor": "token", "class": "release", "outcome": "ok"}, [0, 1, 0]),
        ],
    ),
    ("authoring.versions", "flow", "versions", [({}, [1, 1, 0])]),
    ("authoring.noop_versions", "flow", "versions", [({}, [0, 1, 0])]),
    ("preview.requests", "flow", "requests", [({}, [2, 0, 0])]),
    ("content.records", "stock", "entries", [({}, [40, 41, 41])]),
    ("content.published_entries", "stock", "entries", [({}, [38, 38, 39])]),
    ("content.published_bytes", "stock", "bytes", [({}, [150_000, 150_000, 152_000])]),
    ("content.index_bytes", "stock", "bytes", [({}, [64_000, 64_000, 65_000])]),
    ("history.versions", "stock", "versions", [({}, [300, 301, 301])]),
    ("history.bytes", "stock", "bytes", [({}, [2_000_000, 2_010_000, 2_010_000])]),
    ("tokens.active", "stock", "tokens", [({}, [1, 1, 1])]),
    ("assets.stored_count", "stock", "blobs", [({}, [12, 12, 13])]),
    ("assets.stored_bytes", "stock", "bytes", [({}, [5_000_000, 5_000_000, 6_000_000])]),
    (
        "assets.requests",
        "flow",
        "requests",
        [
            ({"kind": "image", "cache": "hit"}, [50, 60, 40]),
            ({"kind": "image", "cache": "miss"}, [2, 1, 0]),
        ],
    ),
    ("assets.egress_bytes", "flow", "bytes", [({}, [3_000_000, 3_500_000, 2_500_000])]),
]


def usage_report(grain: str = "hour") -> dict[str, Any]:
    """`USAGE_METERS` as `GET …/usage` answers: three hours of 18 Oct 2026, or three days."""
    if grain == "day":
        buckets = [f"2026-10-{d}T00:00:00Z" for d in (16, 17, 18)]
        start, end = buckets[0], "2026-10-19T00:00:00Z"
    else:
        buckets = [f"2026-10-18T{h}:00:00Z" for h in (10, 11, 12)]
        start, end = buckets[0], "2026-10-18T13:00:00Z"
    meters = []
    for name, kind, unit, series in USAGE_METERS:
        meter: dict[str, Any] = {"meter": name, "kind": kind, "unit": unit}
        meter["series"] = [
            {"dims": dims, "values": values, "total": _total(meter, values)}
            for dims, values in series
        ]
        meters.append(meter)
    return {"from": start, "to": end, "grain": grain, "buckets": buckets, "meters": meters}


def _total(meter: dict[str, Any], values: list[int] | None = None) -> int:
    """A series' total as authoring forms it; with no ``values``, the meter's summed series."""
    if values is None:
        values = [sum(col) for col in zip(*(s["values"] for s in meter["series"]), strict=True)]
    if meter["kind"] == "flow":
        return sum(values)
    if meter["kind"] == "stock":
        return values[-1] if values else 0
    return max(values, default=0)


def _s3_error(status: int, code: str, message: str) -> Response:
    """Object storage's own refusal: XML, not a problem document."""
    body = f'<?xml version="1.0"?><Error><Code>{code}</Code><Message>{message}</Message></Error>'
    return Response(body, status=status, content_type="application/xml")


def _sniff(data: bytes) -> tuple[str, int | None, int | None] | None:
    """What the server would say the bytes are: enough of its allowlist for tests."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", None, None
    if data.startswith(b"%PDF-"):
        return "application/pdf", None, None
    if data.lstrip().startswith(b"<svg"):
        return "image/svg+xml", None, None
    return None
