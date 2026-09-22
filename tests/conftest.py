"""Fixtures: the fake BFF from ``coho_management_sdk.testing`` and a client pointed at it."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from coho_management_sdk import Coho
from coho_management_sdk.profiles import Profile
from coho_management_sdk.testing import FakeBff


@pytest.fixture
def bff(httpserver: HTTPServer) -> FakeBff:
    return FakeBff(httpserver)


@pytest.fixture
def profile(bff: FakeBff) -> Profile:
    return Profile(name="test", url=bff.url, token_store="file")


@pytest.fixture
def coho(bff: FakeBff) -> Coho:
    return Coho(bff.url, token="test-token")
