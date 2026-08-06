"""T14b: registration, current user, user search and logout.

Requests go through ``calton.main.create_app`` so the wiring is covered too.
Expected values come from ``tests/fixtures/go_users.json``, recorded from a
running Go server by ``scripts/dump_go_users.py``.

The largest group here is ``TestUserListIsNotPaginated``. That endpoint is the
one Phase 1 list that skips the generic handler, so all the usual list rules are
inverted for it, and every one of those inversions is something a consistency
pass would "fix".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
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
from calton.models.session import Session
from calton.models.user import User

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_users.json"

PASSWORD = "12345678"

#: Mirrors the seed the probe prepares: alice and bob undiscoverable, carol
#: discoverable by name, dave by email.
SEED = [
    {"id": 900, "username": "alice"},
    {"id": 901, "username": "bob"},
    {
        "id": 902,
        "username": "carol",
        "name": "Carol Danvers",
        "email": "carol@example.com",
        "discoverable_by_name": True,
    },
    {"id": 903, "username": "dave", "email": "dave@example.com", "discoverable_by_email": True},
]


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
    built = make_session_factory(engine)

    with built() as session:
        for entry in SEED:
            session.add(
                User(
                    id=entry["id"],
                    username=entry["username"],
                    name=entry.get("name", ""),
                    email=entry.get("email"),
                    password=hash_password(PASSWORD, rounds=4),
                    is_admin=False,
                    discoverable_by_name=entry.get("discoverable_by_name", False),
                    discoverable_by_email=entry.get("discoverable_by_email", False),
                    overdue_tasks_reminders_time="09:00",
                )
            )
        session.commit()
    return built


@pytest.fixture
def app(settings: Settings, engine: Engine, factory: sessionmaker[DbSession]) -> FastAPI:
    return create_app(settings=settings, engine=engine)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as opened:
        yield opened


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    response = client.post("/api/v1/login", json={"username": "alice", "password": PASSWORD})
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


class TestWiring:
    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/register", "post"),
            ("/api/v1/user", "get"),
            ("/api/v1/users", "get"),
            ("/api/v1/user/logout", "post"),
        ],
    )
    def test_route_is_in_the_generated_contract(self, app: FastAPI, path: str, method: str) -> None:
        assert method in app.openapi()["paths"].get(path, {}), path

    def test_user_list_declares_no_pagination_parameters(self, app: FastAPI) -> None:
        """Upstream ignores page/per_page, so declaring them would document a
        capability the endpoint does not have."""
        operation = app.openapi()["paths"]["/api/v1/users"]["get"]
        names = {parameter["name"] for parameter in operation.get("parameters", [])}

        assert "page" not in names
        assert "per_page" not in names
        assert "s" in names


class TestRegister:
    def test_creates_an_account_that_can_log_in(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        created = client.post(
            "/api/v1/register",
            json={"username": "newbie", "password": PASSWORD, "email": "newbie@example.com"},
        )

        assert created.status_code == go["register"]["ok"]["status"] == 200
        assert sorted(created.json()) == go["register"]["ok"]["body_keys"]

        logged_in = client.post("/api/v1/login", json={"username": "newbie", "password": PASSWORD})
        assert logged_in.status_code == 200
        assert go["register"]["can_log_in_immediately"] is True

    def test_the_response_echoes_email_unlike_get_user(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/register",
            json={"username": "newbie", "password": PASSWORD, "email": "newbie@example.com"},
        )

        assert created.json()["email"] == "newbie@example.com"
        assert "password" not in created.json()

    @pytest.mark.parametrize("case", ["duplicate_username", "duplicate_email"])
    def test_duplicates_get_their_own_codes(
        self, client: TestClient, go: dict[str, Any], case: str
    ) -> None:
        client.post(
            "/api/v1/register",
            json={"username": "taken", "password": PASSWORD, "email": "taken@example.com"},
        )
        body = {
            "duplicate_username": {
                "username": "taken",
                "password": PASSWORD,
                "email": "other@example.com",
            },
            "duplicate_email": {
                "username": "other",
                "password": PASSWORD,
                "email": "taken@example.com",
            },
        }[case]

        response = client.post("/api/v1/register", json=body)

        assert response.status_code == go["register"][case]["status"]
        assert response.json() == go["register"][case]["body"]

    @pytest.mark.parametrize("case", ["missing_email", "empty_body"])
    def test_a_missing_field_is_400_1004(
        self, client: TestClient, go: dict[str, Any], case: str
    ) -> None:
        """Including a missing *email*, whose message names username and password.

        Upstream wording. Correcting it would change a string clients match on.
        """
        body = {"username": "someone", "password": PASSWORD} if case == "missing_email" else {}

        response = client.post("/api/v1/register", json=body)

        assert response.status_code == go["register"][case]["status"] == 400
        assert response.json() == go["register"][case]["body"]

    def test_a_short_password_is_412_with_invalid_fields(
        self, client: TestClient, go: dict[str, Any]
    ) -> None:
        """412/2002, not the 400 the other registration errors use."""
        expected = go["register"]["short_password"]

        response = client.post(
            "/api/v1/register",
            json={"username": "shortpw", "password": "1", "email": "s@example.com"},
        )

        assert response.status_code == expected["status"] == 412
        assert response.json() == expected["body"]


class TestRegistrationValidators:
    """Driven entirely by the recorded matrix — every case is a measurement."""

    @pytest.fixture
    def usernames(self, go: dict[str, Any]) -> dict[str, Any]:
        matrix: dict[str, Any] = go["register"]["validation"]["usernames"]
        return matrix

    def test_the_matrix_covers_both_outcomes(self, usernames: dict[str, Any]) -> None:
        """Guards the guard: a matrix that is all-accept would assert nothing."""
        outcomes = {case["accepted"] for case in usernames.values()}

        assert outcomes == {True, False}

    def test_every_recorded_username_gets_the_recorded_verdict(
        self, client: TestClient, usernames: dict[str, Any]
    ) -> None:
        """Only whitespace and a dot are refused — ``@``, ``/``, ``:``, ``#`` and
        non-ASCII are all fine. Tightening this to alphanumeric would lock out
        accounts an imported Calton database legitimately holds."""
        for index, (username, expected) in enumerate(usernames.items()):
            response = client.post(
                "/api/v1/register",
                json={
                    "username": username,
                    "password": PASSWORD,
                    "email": f"u{index}@example.com",
                },
            )

            assert (response.status_code == 200) is expected["accepted"], username

    @pytest.mark.parametrize("password", ["1", "xxxx", "xxxxxxx", "xxxxxxxx"])
    def test_password_length_boundary_matches(
        self, client: TestClient, go: dict[str, Any], password: str
    ) -> None:
        """Seven characters is refused, eight accepted."""
        expected = go["register"]["validation"]["passwords"][password]

        response = client.post(
            "/api/v1/register",
            json={
                "username": f"pw{len(password)}",
                "password": password,
                "email": f"pw{len(password)}@example.com",
            },
        )

        assert (response.status_code == 200) is expected["accepted"]

    def test_invalid_fields_echoes_the_submitted_value(self, client: TestClient) -> None:
        """⚠️ Upstream echoes the value back, so a rejected password appears in
        the response in plaintext. Copied for parity and pinned here so the
        behaviour is visible rather than incidental — anything logging error
        bodies is logging passwords."""
        response = client.post(
            "/api/v1/register",
            json={"username": "pwecho", "password": "secret1", "email": "e@example.com"},
        )

        assert response.json()["invalid_fields"] == [
            "password: secret1 does not validate as bcrypt_password"
        ]

    def test_multiple_failures_are_reported_together_in_field_order(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/register",
            json={"username": "bad user", "password": PASSWORD, "email": "notanemail"},
        )

        assert response.json()["invalid_fields"] == [
            "email: notanemail does not validate as email",
            "username: bad user does not validate as username",
        ]

    def test_validation_is_checked_before_the_duplicate_email_check(
        self, client: TestClient
    ) -> None:
        """Measured ordering: an invalid username beats a taken email."""
        client.post(
            "/api/v1/register",
            json={"username": "basis", "password": PASSWORD, "email": "dup@example.com"},
        )

        response = client.post(
            "/api/v1/register",
            json={"username": "bad.name", "password": PASSWORD, "email": "dup@example.com"},
        )

        assert response.status_code == 412
        assert response.json()["invalid_fields"] == [
            "username: bad.name does not validate as username"
        ]

    @pytest.mark.parametrize(
        ("field", "payload", "expected"),
        [
            (
                "password",
                {"password": "1", "email": "fresh@example.com"},
                "password: 1 does not validate as bcrypt_password",
            ),
            (
                "email",
                {"password": PASSWORD, "email": "notanemail"},
                "email: notanemail does not validate as email",
            ),
        ],
    )
    def test_validation_is_checked_before_the_duplicate_username_check(
        self, client: TestClient, field: str, payload: dict[str, str], expected: str
    ) -> None:
        """The taken username is reported only once the fields are valid.

        Distinct from the test above: that one leaves the username free and so
        never reaches the username-duplicate branch. Reordering the two checks
        passes that test and fails this one, which is how the ordering is
        actually pinned rather than assumed.
        """
        client.post(
            "/api/v1/register",
            json={"username": "occupied", "password": PASSWORD, "email": "occupied@example.com"},
        )

        response = client.post("/api/v1/register", json={"username": "occupied", **payload})

        assert response.status_code == 412
        assert response.json()["invalid_fields"] == [expected]


class TestCurrentUser:
    def test_body_keys_match_the_reference(self, authed: TestClient, go: dict[str, Any]) -> None:
        response = authed.get("/api/v1/user")

        assert response.status_code == go["current_user"]["status"]
        assert sorted(response.json()) == go["current_user"]["body_keys"]

    def test_settings_keys_match_the_reference(
        self, authed: TestClient, go: dict[str, Any]
    ) -> None:
        assert (
            sorted(authed.get("/api/v1/user").json()["settings"])
            == (go["current_user"]["settings_keys"])
        )

    def test_no_password_field_anywhere_in_the_body(self, authed: TestClient) -> None:
        """The whole reason this endpoint has its own schema."""
        assert "password" not in authed.get("/api/v1/user").text

    def test_no_email_field_either(self, authed: TestClient, go: dict[str, Any]) -> None:
        """Upstream does not return the address here, even to its owner."""
        assert "email" not in authed.get("/api/v1/user").json()
        assert "email" not in go["current_user"]["body_keys"]

    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v1/user")

        assert response.status_code == 401
        assert response.json()["code"] == 11


class TestUserSearch:
    """The search matrix, read case by case out of the recording."""

    def _usernames(self, client: TestClient, term: str) -> list[str]:
        found = client.get("/api/v1/users", params={"s": term}).json()
        return [user["username"] for user in found or []]

    def test_every_recorded_search_returns_the_recorded_users(
        self, authed: TestClient, go: dict[str, Any]
    ) -> None:
        for term, expected in go["user_list"]["search"].items():
            assert self._usernames(authed, term) == expected["usernames"], term

    def test_an_exact_username_matches_even_when_undiscoverable(self, authed: TestClient) -> None:
        """alice has both flags off; her exact username still resolves."""
        assert self._usernames(authed, "alice") == ["alice"]

    def test_a_partial_username_does_not_match_an_undiscoverable_user(
        self, authed: TestClient
    ) -> None:
        """The privacy rule. Substring matching everyone would let any account
        enumerate the whole user table three characters at a time."""
        assert self._usernames(authed, "ali") == []

    def test_a_partial_name_matches_only_with_discoverable_by_name(
        self, authed: TestClient
    ) -> None:
        assert self._usernames(authed, "Danvers") == ["carol"]

    def test_email_matching_is_exact_and_needs_the_email_flag(self, authed: TestClient) -> None:
        """A LIKE here would let an attacker recover addresses character by
        character; ``dave@`` must find nothing."""
        assert self._usernames(authed, "dave@example.com") == ["dave"]
        assert self._usernames(authed, "dave@") == []

    def test_a_name_discoverable_user_is_not_found_by_email(self, authed: TestClient) -> None:
        """carol has an address but only the name flag: the flags are independent."""
        assert self._usernames(authed, "carol@example.com") == []

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/users", params={"s": "alice"}).status_code == 401


class TestUserListIsNotPaginated:
    """The three inversions a consistency pass would undo."""

    def test_an_empty_result_is_null_not_an_empty_list(
        self, authed: TestClient, go: dict[str, Any]
    ) -> None:
        response = authed.get("/api/v1/users", params={"s": "nobodyhere"})

        assert response.text.strip() == "null"
        assert response.text.strip() != "[]"
        assert go["user_list"]["no_query"]["body_raw"] == "null"

    def test_no_query_at_all_is_also_null(self, authed: TestClient) -> None:
        """The endpoint never lists every user, however it is called."""
        assert authed.get("/api/v1/users").text.strip() == "null"

    @pytest.mark.parametrize(
        "header",
        ["x-pagination-result-count", "x-pagination-total-pages", "access-control-expose-headers"],
    )
    def test_sends_no_pagination_headers(
        self, authed: TestClient, go: dict[str, Any], header: str
    ) -> None:
        """Absent, not zero. The frontend's ContractViolationError on missing
        headers belongs on its exemption list — adding them here forks from Go."""
        response = authed.get("/api/v1/users", params={"s": "alice"})

        assert header not in response.headers
        assert header not in go["user_list"]["header_names"]

    def test_page_and_per_page_are_ignored(self, authed: TestClient, go: dict[str, Any]) -> None:
        paged = authed.get("/api/v1/users", params={"s": "carol", "page": 2, "per_page": 1})
        unpaged = authed.get("/api/v1/users", params={"s": "carol"})

        assert paged.text == unpaged.text
        assert go["user_list"]["pagination_ignored"]["same_body"] is True

    def test_pagination_parameters_do_not_become_a_validation_error(
        self, authed: TestClient
    ) -> None:
        """Ignored means accepted-and-discarded, not rejected."""
        assert authed.get("/api/v1/users", params={"s": "carol", "page": 2}).status_code == 200


class TestLogout:
    def test_returns_the_reference_message(self, authed: TestClient, go: dict[str, Any]) -> None:
        response = authed.post("/api/v1/user/logout")

        assert response.status_code == go["logout"]["ok"]["status"]
        assert response.json() == go["logout"]["ok"]["body"]

    def test_clears_the_refresh_cookie(self, authed: TestClient) -> None:
        header = authed.post("/api/v1/user/logout").headers["set-cookie"]

        assert f"{sessions.REFRESH_COOKIE_NAME}=" in header
        assert f"Path={sessions.REFRESH_COOKIE_PATH}" in header
        assert "Max-Age=0" in header or "expires=Thu, 01 Jan 1970" in header.lower()

    def test_deletes_the_session_row(
        self, authed: TestClient, factory: sessionmaker[DbSession], go: dict[str, Any]
    ) -> None:
        with factory() as session:
            before = session.query(Session).count()

        authed.post("/api/v1/user/logout")

        with factory() as session:
            after = session.query(Session).count()

        assert before == 1
        assert after == 0
        assert go["logout"]["deletes_session_row_without_cookie"] is True

    def test_the_refresh_cookie_stops_working(self, authed: TestClient) -> None:
        authed.post("/api/v1/user/logout")

        assert authed.post("/api/v1/user/token/refresh").status_code == 401

    def test_the_access_token_still_works_until_it_expires(
        self, authed: TestClient, go: dict[str, Any]
    ) -> None:
        """⚠️ Surprising but measured: logout drops the session, it does not
        revoke issued JWTs. Copied so the two implementations agree on how long a
        stolen access token stays usable."""
        authed.post("/api/v1/user/logout")

        response = authed.get("/api/v1/token/test")

        assert response.status_code == go["logout"]["access_token_still_valid"] == 200

    def test_requires_authentication(self, client: TestClient, go: dict[str, Any]) -> None:
        response = client.post("/api/v1/user/logout")

        assert response.status_code == go["logout"]["unauthenticated"]["status"]
        assert response.json() == go["logout"]["unauthenticated"]["body"]
