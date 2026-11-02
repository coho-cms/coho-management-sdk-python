"""project.webhooks: endpoints Coho calls, and each one's log (coho-data doc 70)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coho_management_sdk import Coho, Project
from coho_management_sdk import errors as e
from coho_management_sdk.testing import ACCOUNT, PROJECT, WEBHOOK_LIMIT, FakeBff

HOOKS = f"/api/v1/accounts/{ACCOUNT}/projects/{PROJECT}/webhooks"


@pytest.fixture
def project(coho: Coho) -> Project:
    return coho.account("acme").project(PROJECT)


def test_create_answers_the_secret_once_and_list_leaves_the_url_out(
    project: Project, bff: FakeBff
) -> None:
    created = project.webhooks.create(
        url="https://build.example.com/hooks/coho?token=abc",
        events=["environment.promoted", "tag.*"],
        refs=["prod"],
        headers={"Authorization": "Bearer xyz"},
        description="Rebuild the site",
    )
    req = bff.last()
    assert req.method == "POST" and req.path == HOOKS
    assert req.get_json() == {
        "url": "https://build.example.com/hooks/coho?token=abc",
        "events": ["environment.promoted", "tag.*"],
        "enabled": True,
        "refs": ["prod"],
        "headers": {"Authorization": "Bearer xyz"},
        "description": "Rebuild the site",
    }
    assert created.secret.startswith("whsec_")
    w = created.webhook
    assert (w.host, w.url, w.refs, w.headers) == (
        "build.example.com",
        "https://build.example.com/hooks/coho?token=abc",
        ["prod"],
        ["Authorization"],
    )
    assert w.enabled and "secret" not in w.raw

    listed = project.webhooks.list()
    assert listed.limit == WEBHOOK_LIMIT
    [only] = listed.webhooks
    assert only.id == w.id and only.url is None and only.host == "build.example.com"
    assert project.webhooks.get(w.id).url == w.url


def test_a_webhook_left_to_every_ref_has_no_refs(project: Project, bff: FakeBff) -> None:
    w = project.webhooks.create(url="https://hooks.example.com/", events=["*"], enabled=False)
    assert "refs" not in bff.last().get_json() and bff.last().get_json()["enabled"] is False
    assert w.webhook.refs is None and w.webhook.enabled is False


def test_events_lists_the_types_and_their_groups(project: Project) -> None:
    events = project.webhooks.events()
    assert "environment.promoted" in events.events and "ping" not in events.events
    assert "tag.*" in events.groups


def test_update_sends_only_what_changes(project: Project, bff: FakeBff) -> None:
    w = project.webhooks.create(
        url="https://hooks.example.com/",
        events=["*"],
        refs=["main"],
        headers={"X-Team": "web"},
    ).webhook
    project.webhooks.update(w.id, description="Search index")
    assert bff.last().method == "PATCH"
    assert bff.last().get_json() == {"description": "Search index"}

    # An empty refs list is every ref again; an empty headers object clears them.
    changed = project.webhooks.update(w.id, refs=[], headers={}, enabled=False)
    assert bff.last().get_json() == {"refs": [], "headers": {}, "enabled": False}
    assert changed.refs is None and changed.headers == [] and not changed.enabled
    assert bff.webhook_headers[w.id] == {}


def test_refusals_carry_the_servers_codes(project: Project, bff: FakeBff) -> None:
    with pytest.raises(e.WebhookUrlInvalid):
        project.webhooks.create(url="http://hooks.example.com/", events=["*"])
    with pytest.raises(e.WebhookUrlRefused) as refused:
        project.webhooks.create(url="https://10.0.3.7/", events=["*"])
    assert "10.0.3.7" in refused.value.detail
    with pytest.raises(e.WebhookEventUnknown):
        project.webhooks.create(url="https://hooks.example.com/", events=["entry.saved"])
    with pytest.raises(e.WebhookHeaderReserved):
        project.webhooks.create(
            url="https://hooks.example.com/", events=["*"], headers={"webhook-id": "x"}
        )
    # Each is a WebhookInvalid, to catch them all at once.
    with pytest.raises(e.WebhookInvalid) as invalid:
        project.webhooks.create(url="https://hooks.example.com/", events=[])
    assert invalid.value.code == "WEBHOOK_INVALID"


def test_the_twenty_sixth_webhook_is_refused(project: Project) -> None:
    for i in range(WEBHOOK_LIMIT):
        project.webhooks.create(url=f"https://hooks{i}.example.com/", events=["*"])
    with pytest.raises(e.WebhookLimitReached):
        project.webhooks.create(url="https://one-more.example.com/", events=["*"])


def test_a_deployment_that_cannot_seal_refuses_webhooks(project: Project, bff: FakeBff) -> None:
    bff.webhooks_configured = False
    with pytest.raises(e.WebhooksUnconfigured) as info:
        project.webhooks.create(url="https://hooks.example.com/", events=["*"])
    assert info.value.status == 503 and isinstance(info.value, e.UpstreamUnavailable)


def test_rotate_answers_the_new_secret_and_when_the_old_one_stops(
    project: Project, bff: FakeBff
) -> None:
    created = project.webhooks.create(url="https://hooks.example.com/", events=["*"])
    rotated = project.webhooks.rotate_secret(created.webhook.id)
    assert bff.last().path == f"{HOOKS}/{created.webhook.id}/rotate-secret"
    assert rotated.secret.startswith("whsec_") and rotated.secret != created.secret
    assert rotated.previous_secret_until == "2026-11-03T14:00:00Z"


def test_test_queues_a_ping_whose_delivery_shows_the_attempt(project: Project) -> None:
    w = project.webhooks.create(url="https://hooks.example.com/", events=["*"]).webhook
    delivery_id = project.webhooks.test(w.id)
    detail = project.webhooks.delivery(w.id, delivery_id)
    assert detail.delivery.type == "ping" and detail.delivery.state == "succeeded"
    [attempt] = detail.attempts
    assert attempt.response_status == 200 and attempt.duration_ms > 0
    assert attempt.request_headers["content-type"] == "application/json"

    project.webhooks.update(w.id, enabled=False)
    with pytest.raises(e.WebhookInvalid, match="disabled"):
        project.webhooks.test(w.id)


def test_deliveries_filter_by_state_type_and_time(project: Project, bff: FakeBff) -> None:
    w = project.webhooks.create(url="https://hooks.example.com/", events=["*"]).webhook
    bff.add_delivery(w.id, type="tag.ready", state="succeeded", status=200)
    late = bff.add_delivery(
        w.id, state="failed", status=None, error_kind="timeout", error="No answer in 15 s"
    )

    log = project.webhooks.deliveries(w.id)
    assert [d.id for d in log.deliveries][0] == late
    failed = project.webhooks.deliveries(
        w.id,
        state="failed",
        type="entries.changed",
        since=datetime(2026, 11, 2, 12, 2, tzinfo=UTC),
        limit=10,
    )
    assert bff.last().args.to_dict() == {
        "state": "failed",
        "type": "entries.changed",
        "since": "2026-11-02T12:02:00+00:00",
        "limit": "10",
    }
    assert [(d.id, d.last_error_kind) for d in failed.deliveries] == [(late, "timeout")]
    older = project.webhooks.deliveries(w.id, before="2026-11-02T12:02:00Z")
    assert [d.type for d in older.deliveries] == ["tag.ready"]

    with pytest.raises(e.CohoError) as info:
        project.webhooks.deliveries(w.id, state="lost")
    assert info.value.code == "BAD_REQUEST"


def test_redeliver_one_or_every_failure_since(project: Project, bff: FakeBff) -> None:
    w = project.webhooks.create(url="https://hooks.example.com/", events=["*"]).webhook
    first = bff.add_delivery(w.id, created_at="2026-11-01T09:00:00Z")
    bff.add_delivery(w.id, created_at="2026-11-02T09:00:00Z")
    bff.add_delivery(w.id, created_at="2026-11-02T10:00:00Z")

    assert project.webhooks.redeliver(w.id, first) == 1
    assert project.webhooks.delivery(w.id, first).delivery.state == "pending"
    # Being sent again: not finished, so not sent a second time over it.
    with pytest.raises(e.CohoError) as info:
        project.webhooks.redeliver(w.id, first)
    assert info.value.code == "BAD_REQUEST"

    assert project.webhooks.redeliver_failed(w.id, "2026-11-02T00:00:00Z") == 2
    assert bff.last().path == f"{HOOKS}/{w.id}/redeliver"
    assert bff.last().args["since"] == "2026-11-02T00:00:00Z"


def test_delete_takes_the_log_with_it(project: Project, bff: FakeBff) -> None:
    w = project.webhooks.create(url="https://hooks.example.com/", events=["*"]).webhook
    project.webhooks.delete(w.id)
    assert bff.last().method == "DELETE" and w.id not in bff.webhook_deliveries
    with pytest.raises(e.NotFound):
        project.webhooks.get(w.id)
