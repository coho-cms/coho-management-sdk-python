"""Assets (doc 40): upload in three steps, then read, edit and delete like entries."""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from coho_management_sdk import (
    AssetsApi,
    AssetTooLarge,
    AssetTypeNotAllowed,
    AssetUploadIncomplete,
    Coho,
    FocalPoint,
    PreconditionRequired,
    UploadRejected,
    VersionConflict,
    sha256_of,
)
from coho_management_sdk.testing import ACCOUNT, PROJECT, FakeBff

REF = f"/api/v1/accounts/{ACCOUNT}/projects/{PROJECT}/refs/dev"


def png(width: int, height: int) -> bytes:
    """A real PNG, small enough to build by hand."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"".join(b"\x00" + b"\x00" * width * 3 for _ in range(height)))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def dev(coho: Coho) -> AssetsApi:
    return coho.account(ACCOUNT).project(PROJECT).ref("dev").assets


def test_upload_is_begin_put_complete(coho: Coho, bff: FakeBff, tmp_path: Path) -> None:
    path = tmp_path / "hero shot.png"
    data = png(40, 30)
    path.write_bytes(data)

    asset = dev(coho).upload(path, slug="hero", title="Hero", focal_point=FocalPoint(0.3, 0.6))

    begin, put, complete = bff.requests[-3:]
    assert begin.path == f"{REF}/assets/uploads"
    assert begin.get_json() == {
        "sha256": hashlib.sha256(data).hexdigest(),
        "byteSize": len(data),
        "filename": "hero shot.png",
        "contentType": "image/png",
    }
    # Straight to the store, with the signed headers and without the BFF's credential.
    assert put.path.startswith("/fake-s3/")
    assert put.get_data() == data
    assert (
        put.headers["x-amz-checksum-sha256"]
        == base64.b64encode(hashlib.sha256(data).digest()).decode()
    )
    assert put.headers["x-amz-tagging"] == "coho-lifecycle=staging"
    assert put.headers["Content-Length"] == str(len(data))
    assert "Authorization" not in put.headers
    assert "Transfer-Encoding" not in put.headers
    assert complete.get_json() == {
        "slug": "hero",
        "title": "Hero",
        "focalPoint": {"x": 0.3, "y": 0.6},
    }

    assert asset.slug == "hero"
    assert asset.file.media_type == "image/png"
    assert (asset.file.width, asset.file.height) == (40, 30)
    assert asset.file.filename == "hero shot.png"
    assert asset.focal_point == FocalPoint(0.3, 0.6)
    assert asset.url is not None and asset.url.endswith("/hero%20shot.png")
    assert asset.image_url is not None and "/i/" in asset.image_url
    assert asset.etag == '"a1"'


def test_bytes_the_project_holds_are_not_sent_again(coho: Coho, bff: FakeBff) -> None:
    data = png(4, 4)
    dev(coho).upload(data, slug="one", filename="one.png")
    sent = len([r for r in bff.requests if r.path.startswith("/fake-s3/")])

    again = dev(coho).upload(data, slug="two", filename="two.png")

    assert len([r for r in bff.requests if r.path.startswith("/fake-s3/")]) == sent
    assert again.file.sha256 == hashlib.sha256(data).hexdigest()


def test_bytes_need_a_filename(coho: Coho) -> None:
    with pytest.raises(ValueError):
        dev(coho).upload(png(2, 2), slug="nameless")


def test_a_file_over_the_limit_is_refused_before_anything_is_sent(
    coho: Coho, bff: FakeBff, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AssetsApi, "MAX_BYTES", 10)
    assets = dev(coho)
    before = len(bff.requests)
    with pytest.raises(AssetTooLarge) as caught:
        assets.upload(png(8, 8), slug="big", filename="big.png")
    assert caught.value.limit == 10
    assert len(bff.requests) == before


def test_the_store_refusing_other_bytes_is_upload_rejected(coho: Coho) -> None:
    assets = dev(coho)
    upload = assets.begin_upload(
        sha256=hashlib.sha256(b"real").hexdigest(), byte_size=4, filename="x.png"
    )
    with pytest.raises(UploadRejected) as caught:
        assets.put_bytes(upload, b"fake", length=4)
    assert caught.value.storage_code == "BadDigest"
    assert "X-Amz-Signature" not in str(caught.value)


def test_completing_before_the_put_is_incomplete(coho: Coho) -> None:
    assets = dev(coho)
    data = png(3, 3)
    upload = assets.begin_upload(
        sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data), filename="x.png"
    )
    with pytest.raises(AssetUploadIncomplete):
        assets.complete_upload(upload.id, slug="early")


def test_what_the_bytes_are_decides_not_the_name(coho: Coho) -> None:
    with pytest.raises(AssetTypeNotAllowed):
        dev(coho).upload(b"<!doctype html>", slug="page", filename="innocent.png")


def test_list_get_put_delete(coho: Coho) -> None:
    assets = dev(coho)
    first = assets.upload(png(5, 5), slug="b-second", filename="b.png", tags=["x"])
    assets.upload(png(6, 6), slug="a-first", filename="a.png")

    assert [a.slug for a in assets.list().assets] == ["a-first", "b-second"]
    assert [a.slug for a in assets.iterate(page_size=1)] == ["a-first", "b-second"]

    fetched = assets.get(first.id)
    assert fetched.tags == ["x"]

    fetched.alt = "Alternative"
    edited = assets.put(fetched)
    assert edited.alt == "Alternative"
    assert edited.tags == ["x"]
    assert edited.file == fetched.file

    with pytest.raises(VersionConflict):
        assets.put(first.id, title="stale", if_match=first.etag)
    with pytest.raises(PreconditionRequired):
        assets.put(first.id, title="no precondition")
    assert assets.put(first.id, title="forced", force=True).title == "forced"

    assert assets.delete(assets.get(first.id)) == '"tombstone"'
    assert [a.slug for a in assets.list().assets] == ["a-first"]


def test_a_listed_asset_carries_an_etag_from_its_version(coho: Coho) -> None:
    assets = dev(coho)
    assets.upload(png(2, 2), slug="listed", filename="l.png")
    listed = assets.list().assets[0]
    assert listed.etag == f'"{listed.version}"'
    assert assets.put(listed, title="from the list").title == "from the list"


def test_sha256_of_streams_a_file_and_hashes_bytes(tmp_path: Path) -> None:
    data = b"x" * (3 * 1024 * 1024 + 7)
    path = tmp_path / "big.bin"
    path.write_bytes(data)
    expected = (hashlib.sha256(data).hexdigest(), len(data))
    assert sha256_of(path) == expected
    assert sha256_of(data) == expected
