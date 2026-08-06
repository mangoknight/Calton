"""What a client sees when a project hierarchy is corrupt.

``CyclicHierarchyError`` is raised deep in the permission layer (T11) and shaped into a
response by T04's error layer. Those two halves were written on separate branches against
a written contract, so this asserts they actually meet: a 500 carrying the v1 fallback
body, no business error code, and the diagnosis in the log instead.

Reaching the real thing needs a cycle in the database, which the permission tests already
cover. What is being checked here is the wiring, so the error is raised directly.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from calton.config import Settings
from calton.main import create_app
from calton.permissions.project import CyclicHierarchyError


@pytest.fixture
def app() -> FastAPI:
    built = create_app(Settings())

    # ⚠️ A probe path, not the real one. This used to register itself at
    # /api/v1/projects/{project_id}, which worked only while no project router existed.
    # Now that T16 mounts one, FastAPI matches in registration order and the real route
    # wins — the probe never ran and these assertions saw a 401 instead of the 500 they
    # are about. The underscore prefix keeps it out of the way of any real route.
    @built.get("/api/v1/_cyclic_project/{project_id}")
    def read_project(project_id: int) -> dict[str, int]:
        raise CyclicHierarchyError(project_id=project_id, depth=512)

    return built


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # TestClient re-raises server exceptions by default, which would assert on the raise
    # rather than on the response the client would actually receive.
    return TestClient(app, raise_server_exceptions=False)


def test_it_becomes_a_500(client: TestClient) -> None:
    assert client.get("/api/v1/_cyclic_project/7").status_code == 500


def test_the_body_is_the_v1_fallback_shape(client: TestClient) -> None:
    """The v1 contract for an unexpected failure: a bare message, nothing else."""
    response = client.get("/api/v1/_cyclic_project/7")

    assert response.json() == {"message": "Internal Server Error"}


def test_it_carries_no_business_error_code(client: TestClient) -> None:
    """Corrupt data is not a business error. A code would imply clients should handle it."""
    body = client.get("/api/v1/_cyclic_project/7").json()

    assert "code" not in body


def test_the_response_is_json(client: TestClient) -> None:
    """Starlette's default is plain text, which makes MCP clients throw inside JSON.parse."""
    response = client.get("/api/v1/_cyclic_project/7")

    assert response.headers["content-type"].startswith("application/json")


def test_nothing_diagnostic_leaks_to_the_client(client: TestClient) -> None:
    body = client.get("/api/v1/_cyclic_project/7").text

    assert "cycle" not in body.lower()
    assert "parent_project_id" not in body


def test_the_exception_still_carries_the_diagnosis(caplog: pytest.LogCaptureFixture) -> None:
    """The response says nothing, so the exception and the log have to say everything."""
    with caplog.at_level(logging.ERROR), pytest.raises(CyclicHierarchyError) as raised:
        raise CyclicHierarchyError(project_id=7, depth=512)

    assert raised.value.project_id == 7
    assert raised.value.depth == 512
    assert "cycle" in str(raised.value)
