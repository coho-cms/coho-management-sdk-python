from __future__ import annotations

import pytest

from coho_sdk import Coho, Resolution
from coho_sdk import errors as e
from coho_sdk.testing import ACCOUNT, ACTOR, ENTRY, PROJECT, FakeBff


def test_me_and_account_by_name(coho: Coho, bff: FakeBff) -> None:
    me = coho.me()
    assert me.email == "ada@acme.example"
    acct = coho.account("acme")  # case-insensitive name
    assert acct.id == ACCOUNT and acct.role == "admin"
    assert bff.last().headers["Authorization"] == "Bearer test-token"
    assert coho.account(ACCOUNT).id == ACCOUNT


def test_unknown_account_is_not_found(coho: Coho) -> None:
    with pytest.raises(e.NotFound):
        coho.account("nobody")


def test_bad_token_is_unauthenticated(bff: FakeBff) -> None:
    with pytest.raises(e.Unauthenticated) as info:
        Coho(bff.url, token="wrong").me()
    assert info.value.code == "UNAUTHENTICATED"


def test_create_project_posts_to_the_account_collection(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").projects.create("Marketing site")
    assert project.id == PROJECT
    assert project.name == "Marketing site"
    req = bff.last()
    assert req.path == f"/api/v1/accounts/{ACCOUNT}/projects"
    assert req.get_json() == {"name": "Marketing site"}  # organizationId comes from the path (BF-3)


def test_refs_encode_slashes(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    ref = project.refs.get("feature/pricing")
    assert ref.kind == "feature_branch" and ref.description == "New pricing"
    # The fake server decodes paths before matching, so assert on the wire form the SDK builds.
    assert project.ref("feature/pricing").path.endswith("/refs/feature%2Fpricing")
    with pytest.raises(e.RefNotFound) as info:
        project.refs.get("nope")
    assert info.value.ref == "nope"


def test_tags_are_refs_of_kind_tag(coho: Coho) -> None:
    project = coho.account("acme").project(PROJECT)
    assert [t.name for t in project.tags.list()] == ["v1.0.0"]
    tag = project.tags.create("v1.1.0", branch="v0.0.x", description="notes")
    assert tag.at == 42 and tag.warnings[0].code == "PENDING_FORWARD_PORT"


def test_branch_create_and_name_taken(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    b = project.branches.create("feature/x", from_ref="v0.0.x", description="d")
    assert b.branched_at == 42
    assert bff.last().get_json()["kind"] == "feature_branch"
    with pytest.raises(e.RefNameTaken):
        project.branches.create("taken", from_ref="v0.0.x")


def test_entry_etag_round_trip(coho: Coho, bff: FakeBff) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    post = dev.entries.get(ENTRY)
    assert post.etag == '"v1"'
    post.fields["title"]["en-US"] = "Changed"
    updated = dev.entries.put(post)
    assert bff.last().headers["If-Match"] == '"v1"'
    assert updated.etag == '"v2"' and updated.fields["title"]["en-US"] == "Changed"
    # The stale object now conflicts.
    with pytest.raises(e.VersionConflict):
        dev.entries.put(post)


def test_put_without_etag_is_refused_locally(coho: Coho, bff: FakeBff) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    before = len(bff.requests)
    with pytest.raises(e.PreconditionRequired):
        dev.entries.put(ENTRY, {"title": {"en-US": "x"}})
    assert len(bff.requests) == before  # nothing was sent


def test_put_force_reads_then_writes(coho: Coho, bff: FakeBff) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    updated = dev.entries.put(ENTRY, {"title": {"en-US": "forced"}}, force=True)
    assert updated.fields["title"]["en-US"] == "forced"
    methods = [r.method for r in bff.requests[-2:]]
    assert methods == ["GET", "PUT"]


def test_delete_returns_tombstone_etag(coho: Coho) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    post = dev.entries.get(ENTRY)
    assert dev.entries.delete(post) == '"tombstone"'


def test_create_entry_errors_carry_context(coho: Coho) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    with pytest.raises(e.SlugConflict) as info:
        dev.entries.create(type="blogPost", slug="taken", fields={"title": {}})
    assert info.value.context["slug"] == "taken"
    with pytest.raises(e.ValidationFailed) as info2:
        dev.entries.create(type="blogPost", slug="ok", fields={})
    assert info2.value.errors == [{"path": "title", "message": "required"}]


def test_iterate_follows_pages(coho: Coho) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    slugs = [x.slug for x in dev.entries.iterate(page_size=2)]
    assert slugs == [f"post-{i}" for i in range(5)]


def test_type_put_create_vs_update(coho: Coho, bff: FakeBff) -> None:
    dev = coho.account("acme").project(PROJECT).ref("dev")
    created = dev.types.put("newType", {"_name": "New", "fields": []})
    assert created.etag == '"n1"'
    existing = dev.types.get("blogPost")
    updated = dev.types.put("blogPost", existing.definition, if_match=existing.etag)
    assert updated.etag == '"t2"'
    with pytest.raises(e.PreconditionRequired):
        dev.types.put("blogPost", existing.definition)


def test_diff_and_merge_conflict_then_resolve(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    d = project.diff("v0.0.x", "feature/pricing")
    assert d.merge_token == "tok-123" and d.conflicts[0].paths == ["title.en-US"]
    with pytest.raises(e.MergeConflict) as info:
        project.merge("feature/pricing", "v0.0.x", expected_token=d.merge_token)
    assert info.value.conflicts[0]["slug"] == "pricing"
    result = project.merge(
        "feature/pricing",
        "v0.0.x",
        message="take ours",
        resolutions=[Resolution(node_id=ENTRY, path="title.en-US", take="source")],
        expected_token=d.merge_token,
    )
    assert result.resolved == 1 and result.merged == 2
    sent = bff.last().get_json()
    assert sent["resolutions"] == [{"nodeId": ENTRY, "take": "source", "path": "title.en-US"}]
    assert sent["expectedToken"] == "tok-123"


def test_promote_waits_on_snapshot_not_ready(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    bff.snapshot_not_ready_times = 2
    waits: list[str] = []
    env = project.environments.promote(
        "prod", "v1.1.0", wait=True, poll=0.01, on_wait=lambda exc, _r: waits.append(exc.code)
    )
    assert env.target == "v1.1.0" and env.previous_target == "v1.0.0"
    assert waits == ["SNAPSHOT_NOT_READY", "SNAPSHOT_NOT_READY"]


def test_promote_no_wait_raises(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    bff.snapshot_not_ready_times = 1
    with pytest.raises(e.SnapshotNotReady):
        project.environments.promote("prod", "v1.1.0", wait=False)


def test_promote_expected_target(coho: Coho) -> None:
    project = coho.account("acme").project(PROJECT)
    with pytest.raises(e.EnvironmentMoved):
        project.environments.promote("prod", "v1.1.0", expected_target="wrong")


def test_rollback_uses_history_and_expected_target(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    env = project.environments.rollback("prod")
    assert env.target == "v0.9.0"
    sent = bff.last().get_json()
    assert sent == {
        "target": "v0.9.0",
        "expectedTarget": "v1.0.0",
        "reason": "rollback from v1.0.0",
    }


def test_roles_and_keys(coho: Coho, bff: FakeBff) -> None:
    project = coho.account("acme").project(PROJECT)
    assert project.roles.list()[0].role == "owner"
    assert project.roles.grant(ACTOR, "maintainer").role == "maintainer"
    issued = project.delivery_keys.issue(label="site", refs=["prod"])
    assert issued.key == "coho_dk_SECRET" and issued.delivery.refs == ["prod"]
    listing = project.delivery_keys.list()
    assert listing.public_refs == ["prod"] and listing.keys[0].refs is None
    with pytest.raises(e.CohoError) as info:
        project.delivery_keys.issue(refs=[])
    assert info.value.code == "BAD_REQUEST"


def test_invite_and_last_admin(coho: Coho) -> None:
    acct = coho.account("acme")
    issued = acct.invitations.create("jane@acme.example", role="member")
    assert issued.token == "secret-invitation-token"
    assert (
        issued.link("https://bff.example/") == "https://bff.example/invite#secret-invitation-token"
    )
    with pytest.raises(e.LastAdmin):
        acct.members.change_role(acct.coho.me().user_id, "member")


def test_entitlements_absent_means_unlimited(coho: Coho) -> None:
    ent = coho.account("acme").entitlements()
    assert ent.max_projects == 5 and ent.max_live_environments is None


def test_export_branch_to_file(coho: Coho, tmp_path) -> None:  # type: ignore[no-untyped-def]
    project = coho.account("acme").project(PROJECT)
    out = tmp_path / "x.ndjson"
    result = project.export("feature/pricing", to=out)
    assert result.seq == 42 and result.bytes_written > 0 and out.read_text().count("\n") == 3


def test_export_tag_follows_redirect(coho: Coho, tmp_path) -> None:  # type: ignore[no-untyped-def]
    project = coho.account("acme").project(PROJECT)
    result = project.export("v1.0.0", to=tmp_path / "t.ndjson")
    assert result.redirected_to is not None and result.redirected_to.endswith("/blob/v1.0.0.ndjson")
    assert (tmp_path / "t.ndjson").read_bytes() == b'{"id":"a"}\n'


def test_preview_goes_through_the_bff(coho: Coho, bff: FakeBff) -> None:
    body = coho.account("acme").project(PROJECT).preview("dev").entries(locale="en-US")
    assert body["entries"][0]["slug"] == "hello"


def test_non_problem_failure_is_transport_error(coho: Coho) -> None:
    from coho_sdk.client import Account

    with pytest.raises(e.TransportError) as info:
        Account(coho, "gateway").projects.create("x")
    assert info.value.code == "HTTP_502"
