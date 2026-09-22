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

import json
from typing import Any

from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

ACCOUNT = "11111111-1111-4111-8111-111111111111"
PROJECT = "22222222-2222-4222-8222-222222222222"
ENTRY = "33333333-3333-4333-8333-333333333333"
ACTOR = "44444444-4444-4444-8444-444444444444"
USER = "55555555-5555-4555-8555-555555555555"

ME = {
    "user": {"id": USER, "displayName": "Ada", "email": "ada@acme.example"},
    "accounts": [{"accountId": ACCOUNT, "accountName": "Acme", "role": "admin", "actorId": ACTOR}],
}

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
        self.snapshot_not_ready_times = 0
        self.entry_version = "v1"
        self.entry_fields: dict[str, Any] = {"title": {"en-US": "Hello"}, "rank": 1}
        self._install()

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
            if request.headers.get("Authorization") != "Bearer test-token":
                return problem(401, "UNAUTHENTICATED", "no credential")
            return ok(ME)

        s.expect_request("/api/v1/me", method="GET").respond_with_handler(me)

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

        # --- preview ---
        s.expect_request(f"{p}/preview/dev/entries", method="GET").respond_with_json(
            {
                "entries": [
                    {"id": ENTRY, "type": "blogPost", "slug": "hello", "fields": {"title": "Hello"}}
                ],
                "page": {"limit": 25, "offset": 0, "hasMore": False},
            }
        )

        # --- a non-problem failure, as a proxy would produce ---
        s.expect_request("/api/v1/accounts/gateway/projects", method="POST").respond_with_data(
            "<html>502 Bad Gateway</html>", status=502, content_type="text/html"
        )

    def _entry(self, i: int) -> dict[str, Any]:
        return {
            "id": f"0000000{i}-0000-4000-8000-000000000000",
            "type": "blogPost",
            "slug": f"post-{i}",
            "version": f"e{i}",
            "fields": {"title": {"en-US": f"Post {i}"}, "rank": i},
        }
