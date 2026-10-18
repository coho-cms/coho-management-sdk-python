"""Usage reports (coho-data doc 41 §7): the calls, and the totals arithmetic the portal shares."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coho_management_sdk import Coho
from coho_management_sdk import errors as e
from coho_management_sdk.models import UsageReport
from coho_management_sdk.testing import ACCOUNT, PROJECT, FakeBff, usage_report


def test_project_usage_reads_the_project_report(coho: Coho, bff: FakeBff) -> None:
    report = coho.account("acme").project(PROJECT).usage()
    assert report.grain == "hour" and len(report.buckets) == 3
    assert report.start == "2026-10-18T10:00:00Z" and report.end == "2026-10-18T13:00:00Z"
    req = bff.last()
    assert req.path == f"/api/v1/accounts/{ACCOUNT}/projects/{PROJECT}/usage"
    assert dict(req.args) == {}


def test_the_query_is_sent_as_the_contract_names_it(coho: Coho, bff: FakeBff) -> None:
    coho.account("acme").project(PROJECT).usage(
        grain="day",
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end="2026-10-19T00:00:00Z",
        meters=["delivery.requests", "delivery.bytes"],
    )
    assert dict(bff.last().args) == {
        "grain": "day",
        "from": "2026-10-01T00:00:00+00:00",
        "to": "2026-10-19T00:00:00Z",
        "meter": "delivery.requests,delivery.bytes",
    }


def test_a_query_the_data_cannot_answer_is_refused(coho: Coho) -> None:
    with pytest.raises(e.CohoError) as info:
        coho.account("acme").project(PROJECT).usage(grain="minute")
    assert info.value.code == "USAGE_QUERY_INVALID" and info.value.status == 400


def test_each_kind_totals_its_own_way() -> None:
    report = UsageReport.from_dict(usage_report())
    assert report.total("delivery.requests") == 342  # a flow sums
    assert report.total("delivery.peak_rpm") == 55  # a peak takes the largest period
    assert report.total("content.records") == 41  # a stock takes the latest
    assert report.total("no.such.meter") == 0
    refused = report.total("delivery.requests", lambda d: d.get("outcome") == "refused")
    assert refused == 2


def test_a_flow_splits_by_one_dimension() -> None:
    report = UsageReport.from_dict(usage_report())
    by_class = report.totals_by("management.requests", "class", lambda d: d["actor"] == "token")
    assert by_class == {"read": 60, "release": 1}
    assert report.per_bucket("management.requests") == [24, 25, 26]


def test_delivery_by_key_finds_the_client_that_is_not_caching() -> None:
    keys = UsageReport.from_dict(usage_report()).delivery_by_key()
    assert [k.key for k in keys] == ["key-1", "none", "key-2"]
    busy, keyless, refused = keys
    assert (busy.requests, busy.not_modified, busy.bytes) == (330, 30, 1_200_000)
    assert busy.ratio == 11.0
    assert keyless.access == "public" and keyless.ratio == 1.0
    assert refused.refused == 2 and refused.ratio is None


def test_account_usage_sums_projects_for_an_admin(coho: Coho, bff: FakeBff) -> None:
    report = coho.account("acme").usage(grain="day")
    assert bff.last().path == f"/api/v1/accounts/{ACCOUNT}/projects/usage"
    assert report.grain == "day" and report.buckets[0] == "2026-10-16T00:00:00Z"
    assert report.total("delivery.requests") == 342
    assert [s.dims for s in report.meter("delivery.requests").series] == [{}]  # type: ignore[union-attr]
    [project] = report.projects
    assert project.id == PROJECT and project.totals["assets.stored_bytes"] == 6_000_000


def test_account_usage_refuses_a_member(coho: Coho, bff: FakeBff) -> None:
    bff.account_admin = False
    with pytest.raises(e.Forbidden) as info:
        coho.account("acme").usage()
    assert info.value.context["permission"] == "account:admin"
