from __future__ import annotations

import httpx

from coho_management_sdk import errors as e


def _resp(status: int, body: str, content_type: str) -> httpx.Response:
    request = httpx.Request("GET", "http://bff/api/v1/x")
    return httpx.Response(
        status, content=body.encode(), headers={"content-type": content_type}, request=request
    )


def test_problem_maps_to_subclass_by_code() -> None:
    r = _resp(
        412,
        '{"code":"VERSION_CONFLICT","title":"Version conflict","status":412,'
        '"expectedVersion":"a","currentVersion":"b"}',
        "application/problem+json",
    )
    err = e.error_for_response(r)
    assert isinstance(err, e.VersionConflict)
    assert err.code == "VERSION_CONFLICT"
    assert err.status == 412
    assert err.expected_version == "a" and err.current_version == "b"


def test_auth_tier_bare_shape_is_still_mapped() -> None:
    r = _resp(409, '{"code":"LAST_ADMIN","detail":"would leave no admin"}', "application/json")
    err = e.error_for_response(r)
    assert isinstance(err, e.LastAdmin)
    assert err.detail == "would leave no admin"
    assert "LAST_ADMIN" in str(err)


def test_unknown_code_is_plain_cohoerror_with_exact_code() -> None:
    r = _resp(400, '{"code":"SOMETHING_NEW","title":"x","status":400}', "application/problem+json")
    err = e.error_for_response(r)
    assert type(err) is e.CohoError
    assert err.code == "SOMETHING_NEW"


def test_status_alone_is_never_the_branch() -> None:
    # Same status, different codes → different classes. PLAN_LIMIT is 409 in auth, 400 in authoring.
    a = e.error_for_response(_resp(409, '{"code":"PLAN_LIMIT","detail":"cap"}', "application/json"))
    b = e.error_for_response(
        _resp(400, '{"code":"PLAN_LIMIT","title":"cap","status":400}', "application/problem+json")
    )
    assert isinstance(a, e.PlanLimit) and isinstance(b, e.PlanLimit)


def test_non_problem_body_becomes_transport_error() -> None:
    err = e.error_for_response(_resp(502, "<html>Bad Gateway</html>", "text/html"))
    assert isinstance(err, e.TransportError)
    assert err.code == "HTTP_502"
    assert "Bad Gateway" in str(err)


def test_merge_conflict_exposes_conflicts() -> None:
    r = _resp(
        400,
        '{"code":"MERGE_CONFLICT","title":"c","status":400,'
        '"conflicts":[{"nodeId":"n","reason":"field","paths":["a.b"]}]}',
        "application/problem+json",
    )
    err = e.error_for_response(r)
    assert isinstance(err, e.MergeConflict)
    assert err.conflicts[0]["paths"] == ["a.b"]


def test_invitation_states_share_a_class() -> None:
    for code in (
        "INVITATION_USED",
        "INVITATION_ACCEPTED",
        "INVITATION_REVOKED",
        "INVITATION_EXPIRED",
    ):
        err = e.error_for_response(_resp(409, f'{{"code":"{code}"}}', "application/json"))
        assert isinstance(err, e.InvitationUnusable)
        assert err.code == code
