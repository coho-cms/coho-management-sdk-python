"""The distribution itself: what a release pipeline would otherwise find out too late."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

import coho_management_sdk


def test_version_is_a_single_source_of_truth() -> None:
    """`_version.py` is what hatchling reads, so the two can never disagree."""
    try:
        installed = version("coho-management-sdk")
    except PackageNotFoundError:  # pragma: no cover - only when running from a bare checkout
        pytest.skip("coho-management-sdk is not installed in this environment")
    assert installed == coho_management_sdk.__version__


def test_the_package_is_typed() -> None:
    """`py.typed` must ship, or every consumer's type checker silently ignores us."""
    assert (Path(coho_management_sdk.__file__).parent / "py.typed").is_file()


def test_public_names_are_importable() -> None:
    for name in coho_management_sdk.__all__:
        assert hasattr(coho_management_sdk, name), f"__all__ names {name}, which does not exist"


def test_testing_helpers_are_not_imported_by_default() -> None:
    """`coho_management_sdk.testing` needs pytest-httpserver, which is an extra; importing the
    package must not drag it in."""
    import sys

    assert "coho_management_sdk" in sys.modules
    source = Path(coho_management_sdk.__file__).read_text()
    assert "testing" not in source.split("__all__")[0]
