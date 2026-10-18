# Testing against a fake BFF

`coho_management_sdk.testing.FakeBff` is the contract-shaped fake the SDK's own tests use. It
runs on a real port via `pytest-httpserver`, so your code goes through real HTTP.

```bash
pip install 'coho-management-sdk[testing]'
```

```python
from coho_management_sdk import Coho
from coho_management_sdk.testing import ACCOUNT, ENTRY, PROJECT, FakeBff

def test_my_script(httpserver):
    bff = FakeBff(httpserver)
    coho = Coho(bff.url, token="test-token")     # the only token the fake accepts

    dev = coho.account("acme").project(PROJECT).ref("dev")
    post = dev.entries.get(ENTRY)
    post.fields["rank"] = 2
    dev.entries.put(post)

    assert bff.last().headers["If-Match"] == '"v1"'
    assert bff.last().get_json()["fields"]["rank"] == 2
```

What it encodes:

- `/api/v1/me` for `test-token` (account `Acme`, role `admin`); anything else is `401`.
- Project `PROJECT` with refs `v0.0.x`, `feature/pricing`, `v1.0.0`, `dev`, `prod`.
- Entries on `dev`: five for listing (paginated), and `ENTRY` with a version counter —
  `put` needs `If-Match` (428), a stale one is 412, and each write bumps the version.
- A type `blogPost` with an ETag; `newType` does not exist yet.
- Diff with one conflict and `mergeToken: tok-123`; merge refuses without resolutions.
- Tags are cut at `42` with a `PENDING_FORWARD_PORT` warning. Set `bff.invalid_entries`
  to items shaped like the problem's `invalidEntries`, and a tag cut with `validate`
  is refused with `TAG_CONTENT_INVALID`: `entryCount` counts them all, and at most
  `INVALID_ENTRIES_SHOWN` (100) are listed, in the order given.
- `prod` promotion: set `bff.snapshot_not_ready_times = 2` to get `SNAPSHOT_NOT_READY`
  twice first; `expectedTarget: "wrong"` answers `ENVIRONMENT_MOVED`; target `v9.0.0`
  adds a `SKIPPED_TIER` warning. History for rollback.
- Roles, delivery keys (`coho_dk_SECRET`), invitations (`secret-invitation-token`),
  `LAST_ADMIN` on demoting the only admin, entitlements with an absent limit.
- Usage: `USAGE_METERS` over three hours (or three days with `grain=day`), from
  `usage_report()`: `key-1` asks for each URL eleven times a period, keyless reads once.
  `meter=` narrows them. The account's report sums them; set `bff.account_admin = False` for a member's
  `FORBIDDEN`. A grain other than `hour` or `day` is `USAGE_QUERY_INVALID`.
- Export: a branch streams three NDJSON lines with `X-Coho-Export-Seq: 42`; a tag
  answers `302` to a blob on the same server.
- Assets on `dev`, with a fake object store on the same server. An upload is offered a
  presigned PUT to `/fake-s3/{id}`, which refuses a body whose SHA-256 is not the signed
  `x-amz-checksum-sha256` (`BadDigest`) or a request without `x-amz-tagging`, in S3's
  XML. Bytes it already holds (`bff.blobs`) get no URL. Completing before the PUT is
  `ASSET_UPLOAD_INCOMPLETE`, and anything that is not a PNG, JPEG, PDF or SVG is
  `ASSET_TYPE_NOT_ALLOWED`. Completed assets are in `bff.assets`, listed in slug order,
  with `If-Match` on `put` and `delete` as for entries.
- Account `gateway` answers a `502` HTML page, to test non-problem failures.

`bff.requests` is every request seen by the handlers that record (writes, mostly);
`bff.last()` is the most recent. Register your own routes on `bff.server` (the
`pytest-httpserver` instance) for anything else.
