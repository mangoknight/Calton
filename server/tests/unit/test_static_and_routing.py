"""Serving the built frontend must not interfere with the API.

Two failures are being pinned here, both found by review rather than by CI, and both
invisible unless a static directory actually exists — which it never does in CI, only
inside the Docker image:

1. Mounting the SPA at ``/`` while building the app swallowed every route registered
   afterwards. ``GET /api/v1/info`` returned 404 purely because ``include_router`` ran
   after the mount.
2. ``StaticFiles(html=True)`` does not fall back to ``index.html`` for arbitrary paths,
   so reloading a client-side route such as ``/projects/1`` 404'd. That threatens AC-8.

The order of these tests matters: the API-404 assertion exists so that fixing (2) cannot
quietly turn an unknown API path into a 200 of HTML, which would be far worse than the
bug being fixed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from calton.config import Settings
from calton.main import _resolve_static, create_app


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A stand-in for the bundle the Docker image drops next to the package."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>calton</title>")
    (root / "assets" / "app.js").write_text("console.log('app')")
    return root


@pytest.fixture
def app(static_dir: Path) -> FastAPI:
    built = create_app(Settings(), static_dir=static_dir)

    # Registered after create_app, exactly as include_router will be.
    # Deliberately NOT /api/v1/info: create_app now wires the real one (T33), and
    # Starlette matches first-registered, so reusing that path would test the
    # built-in route rather than late registration — passing for the wrong reason.
    @built.get("/api/v1/late-registration-probe")
    def probe() -> dict[str, bool]:
        return {"reached": True}

    return built


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as opened:
        yield opened


class TestApiIsNeverShadowed:
    def test_routes_registered_after_create_app_are_reachable(self, client: TestClient) -> None:
        """The mount-order regression. This is the one that only broke in Docker."""
        response = client.get("/api/v1/late-registration-probe")

        assert response.status_code == 200
        assert response.json() == {"reached": True}

    def test_unknown_api_path_is_a_json_404(self, client: TestClient) -> None:
        """Must stay a JSON 404 — never the SPA's index.html with a 200."""
        response = client.get("/api/v1/does-not-exist")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    def test_unknown_api_path_is_a_json_404_without_a_bundle(self, tmp_path: Path) -> None:
        with TestClient(create_app(Settings(), static_dir=tmp_path / "absent")) as client:
            response = client.get("/api/v1/does-not-exist")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    def test_health_still_works(self, client: TestClient) -> None:
        assert client.get("/health").json()["status"] == "ok"


class TestSpaFallback:
    def test_client_side_route_serves_the_app_shell(self, client: TestClient) -> None:
        """Reloading /projects/1 must return index.html, not 404."""
        response = client.get("/projects/1")

        assert response.status_code == 200
        assert "<title>calton</title>" in response.text

    def test_root_serves_the_app_shell(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200

    def test_real_assets_are_served(self, client: TestClient) -> None:
        response = client.get("/assets/app.js")

        assert response.status_code == 200
        assert "console.log" in response.text

    def test_no_fallback_without_a_bundle(self, tmp_path: Path) -> None:
        """With nothing to serve, a client route stays a 404 rather than erroring."""
        with TestClient(create_app(Settings(), static_dir=tmp_path / "absent")) as client:
            assert client.get("/projects/1").status_code == 404

    @pytest.mark.parametrize(
        "path",
        ["/../pyproject.toml", "/assets/../../pyproject.toml", "/%2e%2e/pyproject.toml"],
    )
    def test_traversal_over_http_never_leaks_a_file(self, client: TestClient, path: str) -> None:
        response = client.get(path)

        assert "[project]" not in response.text


class TestStaticResolution:
    """The traversal guard, tested directly.

    Over HTTP the client normalizes ``..`` away before the request is sent, so those
    tests would pass even with no guard at all. These call the resolver itself.
    """

    def test_a_real_asset_resolves(self, static_dir: Path) -> None:
        assert _resolve_static(static_dir, "/assets/app.js") == static_dir / "assets" / "app.js"

    @pytest.mark.parametrize(
        "path",
        [
            "/../pyproject.toml",
            "/assets/../../pyproject.toml",
            "/../../etc/passwd",
            "//etc/passwd",
        ],
    )
    def test_paths_escaping_the_bundle_resolve_to_nothing(
        self, static_dir: Path, path: str
    ) -> None:
        assert _resolve_static(static_dir, path) is None

    def test_a_directory_is_not_served_as_a_file(self, static_dir: Path) -> None:
        assert _resolve_static(static_dir, "/assets") is None

    def test_a_missing_file_resolves_to_nothing(self, static_dir: Path) -> None:
        assert _resolve_static(static_dir, "/assets/nope.js") is None
