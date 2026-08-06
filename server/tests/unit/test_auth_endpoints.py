"""The authentication endpoints, as mounted on the real application.

Every request here goes through ``calton.main.create_app``, not a hand-built
FastAPI instance. That is the point: this project has shipped three modules whose
unit tests were green while the feature 404'd on the real app because nothing
called ``include_router``. ``TestWiring`` asserts against
``app.openapi()["paths"]`` — scanning ``app.routes`` misses routers merged by
``include_router``, whose entries have no ``.path``.

Expected statuses and bodies come from ``tests/fixtures/go_jwt.json``, recorded
from a running Go server by ``scripts/dump_go_jwt.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from calton.auth import sessions
from calton.auth.password import hash_password
from calton.config import Settings
from calton.db.base import Base
from calton.db.session import build_engine
from calton.db.session import session_factory as make_session_factory
from calton.main import create_app
from calton.models.user import User

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_jwt.json"

PASSWORD = "12345678"


@pytest.fixture(scope="module")
def go() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text())
    return loaded


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "service": {"secret": "testsecrettestsecrettestsecret12"},
            "database": {"path": str(tmp_path / "calton.db")},
        }
    )


@pytest.fixture
def engine(settings: Settings) -> Engine:
    built = build_engine(settings)
    Base.metadata.create_all(built)
    return built


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[DbSession]:
    return make_session_factory(engine)


@pytest.fixture
def alice(factory: sessionmaker[DbSession]) -> User:
    with factory() as session:
        user = User(
            id=900,
            username="alice",
            password=hash_password(PASSWORD, rounds=4),
            is_admin=False,
            overdue_tasks_reminders_time="09:00",
        )
        session.add(user)
        session.commit()
        return user


@pytest.fixture
def app(settings: Settings, engine: Engine, alice: User) -> FastAPI:
    return create_app(settings=settings, engine=engine)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as opened:
        yield opened


def _cookie_attributes(header: str) -> dict[str, Any]:
    """Split a Set-Cookie header into its parts, lowercased.

    Both our header and the recorded reference go through this, so a test can
    compare like with like rather than substring-matching a constant it also
    supplied.
    """
    name_value, *rest = header.split(";")
    name, _, _value = name_value.partition("=")

    attributes: dict[str, Any] = {"name": name.strip(), "httponly": False, "secure": False}
    for part in rest:
        key, _, value = part.strip().partition("=")
        key = key.lower()
        if key in ("httponly", "secure"):
            attributes[key] = True
        else:
            attributes[key] = value.lower() if key == "samesite" else value
    return attributes


def login(client: TestClient, **overrides: Any) -> Any:
    payload: dict[str, Any] = {"username": "alice", "password": PASSWORD}
    payload.update(overrides)
    return client.post("/api/v1/login", json=payload)


class TestWiring:
    """The routes exist on the assembled application, not merely in a module."""

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/login", "post"),
            ("/api/v1/token/test", "get"),
            ("/api/v1/user/token", "post"),
            ("/api/v1/user/token/refresh", "post"),
        ],
    )
    def test_route_is_in_the_generated_contract(self, app: FastAPI, path: str, method: str) -> None:
        assert method in app.openapi()["paths"].get(path, {}), path

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/login", "post"),
            ("/api/v1/token/test", "get"),
        ],
    )
    def test_route_declares_a_response_schema(self, app: FastAPI, path: str, method: str) -> None:
        """A handler returning ``dict[str, Any]`` generates an empty schema, which
        passes at runtime but leaves the contract diff with nothing to check."""
        operation = app.openapi()["paths"][path][method]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

        assert schema != {}
        assert "$ref" in schema or "properties" in schema

    def test_login_is_reachable_rather_than_404(self, client: TestClient) -> None:
        assert login(client).status_code != 404


class TestLoginSuccess:
    def test_returns_only_a_token(self, client: TestClient, go: dict[str, Any]) -> None:
        response = login(client)

        assert response.status_code == go["login_ok"]["status"]
        assert sorted(response.json()) == go["login_ok"]["body_keys"]

    def test_sets_no_store(self, client: TestClient, go: dict[str, Any]) -> None:
        assert login(client).headers["cache-control"] == go["login_ok"]["cache_control"]

    def test_verifies_an_upstream_cost_4_bcrypt_hash(self, client: TestClient) -> None:
        """The seed's hashes are ``$2a$04$`` while Calton hashes at cost 11.

        Any hard-coded check on the cost or the ``$2a$``/``$2b$`` prefix fails
        exactly here, which is the point of seeding at a different cost.
        """
        assert login(client).status_code == 200


class TestRefreshCookie:
    def test_name_and_path_match_the_reference_cookie(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        """Both sides parsed from their own header.

        Writing this as ``f"Path={REFRESH_COOKIE_PATH}" in header`` compares the
        constant to itself and passes for *any* value of it — the mutation run
        caught exactly that. The reference's path is read out of the recorded
        header instead.
        """
        ours = _cookie_attributes(login(client).headers["set-cookie"])
        theirs = _cookie_attributes(go["login_ok"]["set_cookie"])

        assert ours["name"] == theirs["name"] == sessions.REFRESH_COOKIE_NAME
        assert ours["path"] == theirs["path"] == sessions.REFRESH_COOKIE_PATH

    def test_is_httponly(self, client: TestClient, go: dict[str, Any]) -> None:
        """Without this a cross-site script can read the long-lived credential."""
        assert _cookie_attributes(login(client).headers["set-cookie"])["httponly"]
        assert _cookie_attributes(go["login_ok"]["set_cookie"])["httponly"]

    def test_samesite_matches_the_reference(self, client: TestClient, go: dict[str, Any]) -> None:
        ours = _cookie_attributes(login(client).headers["set-cookie"])
        theirs = _cookie_attributes(go["login_ok"]["set_cookie"])

        assert ours["samesite"] == theirs["samesite"] == "lax"

    def test_max_age_is_the_ordinary_ttl_not_the_access_ttl(
        self, client: TestClient, settings: Settings
    ) -> None:
        header = login(client).headers["set-cookie"]

        assert f"Max-Age={settings.service.jwtttl}" in header
        assert f"Max-Age={settings.service.jwtttlshort}" not in header

    def test_long_token_lengthens_the_cookie_but_not_the_access_token(
        self, client: TestClient, settings: Settings, go: dict[str, Any]
    ) -> None:
        """Measured: ``long_token`` moves the cookie to 30 days and leaves the
        access token at 600s. The name suggests it does the opposite."""
        import jwt as pyjwt

        issued_at = datetime.now(UTC).timestamp()
        response = login(client, long_token=True)
        claims = pyjwt.decode(response.json()["token"], options={"verify_signature": False})

        assert f"Max-Age={settings.service.jwtttllong}" in response.headers["set-cookie"]
        assert f"Max-Age={settings.service.jwtttllong}" in go["long_token"]["set_cookie"]
        # A second of slack: exp is a truncated integer and the clock is read
        # either side of a real request.
        expected = go["long_token"]["access_ttl_seconds_approx"]
        assert expected - 2 <= claims["exp"] - issued_at <= expected + 1
        assert claims["exp"] - issued_at < settings.service.jwtttllong


class TestLoginFailure:
    def test_a_wrong_password_is_403_not_401(self, client: TestClient, go: dict[str, Any]) -> None:
        """The single most likely thing to get "right" and be wrong: authentication
        failure reads as 401 everywhere else, and upstream answers 403/1011."""
        expected = go["login_failures"]["wrong_password"]
        response = login(client, password="wrong")

        assert response.status_code == expected["status"] == 403
        assert response.json() == expected["body"]

    def test_an_unknown_user_is_answered_identically_to_a_wrong_password(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        """Byte-identical on purpose: the response must not reveal whether the
        account exists. Comparing the two responses to each other is what pins
        it — asserting each against a literal would let both drift together."""
        wrong_password = login(client, password="wrong")
        unknown_user = login(client, username="nosuchuser")

        assert unknown_user.status_code == wrong_password.status_code
        assert unknown_user.json() == wrong_password.json()
        assert unknown_user.json() == go["login_failures"]["unknown_user"]["body"]

    @pytest.mark.parametrize("case", ["missing_password", "empty_body"])
    def test_a_missing_credential_is_400_with_its_own_code(
        self, client: TestClient, go: dict[str, Any], case: str
    ) -> None:
        """400/1004, a different error from 403/1011 — and specifically not the
        412/2002 validation shape a required Pydantic field would produce."""
        expected = go["login_failures"][case]
        body = {"username": "alice"} if case == "missing_password" else {}

        response = client.post("/api/v1/login", json=body)

        assert response.status_code == expected["status"] == 400
        assert response.json() == expected["body"]


class TestTokenTest:
    def test_a_valid_token_is_accepted(self, client: TestClient, go: dict[str, Any]) -> None:
        token = login(client).json()["token"]

        response = client.get("/api/v1/token/test", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == go["token_test"]["with_jwt"]["status"]
        assert response.json() == go["token_test"]["with_jwt"]["body"]

    @pytest.mark.parametrize("case", ["no_header", "tampered_signature", "not_bearer"])
    def test_every_rejection_renders_the_same_401(
        self, client: TestClient, go: dict[str, Any], case: str
    ) -> None:
        """One body for all three. Distinguishing them would tell an attacker
        which of missing, malformed and forged they achieved."""
        token = login(client).json()["token"]
        headers = {
            "no_header": {},
            "tampered_signature": {"Authorization": f"Bearer {token[:-3]}xyz"},
            "not_bearer": {"Authorization": token},
        }[case]

        response = client.get("/api/v1/token/test", headers=headers)

        assert response.status_code == 401
        assert response.json() == go["token_test"][case]["body"]
        assert response.json()["code"] == 11


class TestRefreshEndpoint:
    def test_a_valid_cookie_returns_a_new_token(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        login(client)

        response = client.post("/api/v1/user/token/refresh")

        assert response.status_code == 200
        assert sorted(response.json()) == go["refresh"]["with_cookie"]["body_keys"]

    def test_the_session_survives_but_the_token_id_does_not(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        """sid is stable across a refresh, jti is not. Deleting and re-creating the
        session row would change sid and break any device list keyed on it."""
        import jwt as pyjwt

        first = pyjwt.decode(login(client).json()["token"], options={"verify_signature": False})
        response = client.post("/api/v1/user/token/refresh")
        second = pyjwt.decode(response.json()["token"], options={"verify_signature": False})

        assert (second["sid"] == first["sid"]) is go["refresh"]["keeps_sid"]
        assert (second["jti"] != first["jti"]) is go["refresh"]["reissues_jti"]

    def test_the_cookie_is_rotated(self, client: TestClient, go: dict[str, Any]) -> None:
        before = login(client).headers["set-cookie"]
        after = client.post("/api/v1/user/token/refresh").headers["set-cookie"]

        assert (before != after) is go["refresh"]["with_cookie"]["rotates_cookie"]

    def test_a_replayed_cookie_is_rejected(self, client: TestClient, go: dict[str, Any]) -> None:
        """Refresh tokens are single use. Without this, a cookie captured once
        stays valid for its full 72 hours no matter how often it is refreshed."""
        login(client)
        stale = client.cookies.get(sessions.REFRESH_COOKIE_NAME)
        client.post("/api/v1/user/token/refresh")

        response = client.post(
            "/api/v1/user/token/refresh",
            headers={"Cookie": f"{sessions.REFRESH_COOKIE_NAME}={stale}"},
        )

        assert response.status_code == 401
        assert response.json() == go["refresh"]["replayed_cookie"]["body"]

    @pytest.mark.parametrize("case", ["no_cookie", "garbage_cookie"])
    def test_failures_use_the_bare_message_shape_with_no_code(
        self, client: TestClient, go: dict[str, Any], case: str
    ) -> None:
        """The third 401 layer the design does not describe.

        The middleware's 401 carries ``code: 11`` and a domain error carries its
        own; this one carries no code at all. "Helpfully" adding one diverges.
        """
        expected = go["refresh"][case]
        headers = (
            {} if case == "no_cookie" else {"Cookie": f"{sessions.REFRESH_COOKIE_NAME}=deadbeef"}
        )

        response = client.post("/api/v1/user/token/refresh", headers=headers)

        assert response.status_code == expected["status"] == 401
        assert response.json() == expected["body"]
        assert "code" not in response.json()


class TestLegacyRenewalEndpoint:
    def test_it_refuses_and_points_at_refresh(self, client: TestClient, go: dict[str, Any]) -> None:
        """Not a renewal endpoint despite the name — measured as a 400.

        Implemented as the refusal rather than left unrouted, because a 404 would
        be a different answer than the one upstream gives.
        """
        token = login(client).json()["token"]
        expected = go["user_token_endpoint"]

        response = client.post("/api/v1/user/token", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == expected["status"] == 400
        assert response.json() == expected["body"]
