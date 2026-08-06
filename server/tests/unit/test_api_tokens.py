"""T15: API tokens — hashing, the scope check, and which routes they reach.

Expected values come from ``tests/fixtures/go_api_tokens.json``, recorded by
``scripts/dump_go_api_tokens.py`` against a running Go server.

``TestAcceptanceMatrix`` is the centrepiece: it replays the recorded
(grant, endpoint) → status table against Calton. That table is recorded rather
than summarised because every prose summary of it written so far — including the
design's "JWT-only endpoints" list — has been wrong in at least one cell.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from calton.auth import api_token
from calton.auth.deps import CurrentSubject, route_template
from calton.auth.password import hash_password
from calton.config import Settings
from calton.db.base import Base
from calton.db.session import build_engine
from calton.db.session import session_factory as make_session_factory
from calton.main import create_app
from calton.models.api_token import APIToken
from calton.models.user import User

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_api_tokens.json"

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
    built = make_session_factory(engine)
    with built() as session:
        session.add(
            User(
                id=900,
                username="alice",
                password=hash_password(PASSWORD, rounds=4),
                is_admin=False,
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
def jwt(client: TestClient) -> str:
    response = client.post("/api/v1/login", json={"username": "alice", "password": PASSWORD})
    return str(response.json()["token"])


def mint(client: TestClient, jwt: str, permissions: dict[str, list[str]]) -> str:
    created = client.put(
        "/api/v1/tokens",
        headers={"Authorization": f"Bearer {jwt}"},
        json={
            "title": "probe",
            "permissions": permissions,
            "expires_at": "2027-01-01T00:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


class TestHashing:
    def test_format_matches_the_reference(self, go: dict[str, Any]) -> None:
        minted = api_token.mint()

        assert minted.plaintext.startswith(go["create"]["token_prefix"])
        assert len(minted.plaintext) == go["create"]["token_length"] == 43

    def test_our_digest_matches_one_the_go_server_produced(self, go: dict[str, Any]) -> None:
        """The only assertion here that Go has a vote in.

        Everything else in this class compares mint() against hash_token(), which
        proves we agree with ourselves — a self-consistent change (dropping the
        prefix in *both* places, say) satisfies all of them. This one replays a
        (plaintext, salt, digest) triple the reference server produced, so any
        change to the recipe fails it whether or not it is self-consistent.
        """
        vector = go["known_vector"]

        assert api_token.hash_token(vector["plaintext"], vector["salt"]) == vector["hash"]

    def test_the_tk_prefix_is_part_of_the_hashed_secret(self, go: dict[str, Any]) -> None:
        """Stripping it reads like removing a display artefact. It is not."""
        vector = go["known_vector"]
        without_prefix = vector["plaintext"][len(api_token.TOKEN_PREFIX) :]

        assert api_token.hash_token(without_prefix, vector["salt"]) != vector["hash"]
        assert go["hashing"]["matches"]["full_plaintext_with_prefix_salt_raw"] is True
        assert go["hashing"]["matches"]["without_tk_prefix_salt_raw"] is False

    def test_the_salt_is_used_as_raw_characters(self, go: dict[str, Any]) -> None:
        """Not hex-decoded — and the reference salt is not even valid hex."""
        minted = api_token.mint()

        assert len(minted.salt) == go["hashing"]["salt_length"] == 10
        assert api_token.hash_token(minted.plaintext, minted.salt) == minted.hash

    def test_digest_shape_matches(self, go: dict[str, Any]) -> None:
        minted = api_token.mint()

        assert len(minted.hash) == go["hashing"]["hash_length"] == 100
        assert go["hashing"]["iterations"] == api_token.PBKDF2_ITERATIONS
        assert go["hashing"]["dklen"] == api_token.PBKDF2_DKLEN

    def test_last_eight_is_the_plaintext_suffix(self, go: dict[str, Any]) -> None:
        minted = api_token.mint()

        assert minted.last_eight == minted.plaintext[-8:]
        assert go["hashing"]["last_eight_is_plaintext_suffix"] is True

    def test_two_tokens_never_share_a_salt(self) -> None:
        """A shared salt would make one precomputation usable against every token."""
        salts = {api_token.mint().salt for _ in range(20)}

        assert len(salts) == 20


class TestVerification:
    def test_a_minted_token_verifies(self, factory: sessionmaker[DbSession]) -> None:
        minted = api_token.mint()
        with factory() as session:
            session.add(_row(minted))
            session.commit()

            assert api_token.verify(session, minted.plaintext) is not None

    def test_verification_uses_each_rows_own_salt(self, factory: sessionmaker[DbSession]) -> None:
        """With several candidates sharing a last-eight, only the right one matches.

        Re-hashing every candidate with the first row's salt would authenticate
        the wrong token, or none.
        """
        # Two plaintexts ending in the same eight characters, so the index
        # narrows to both rows and the salts are what tell them apart.
        first = _minted_ending_in("a" * 32, "deadbeef")
        second = _minted_ending_in("b" * 32, "deadbeef")
        assert first.last_eight == second.last_eight
        assert first.salt != second.salt

        with factory() as session:
            session.add(_row(first))
            session.add(_row(second))
            session.commit()

            assert api_token.verify(session, first.plaintext) is not None
            assert api_token.verify(session, second.plaintext) is not None
            # And neither authenticates the other's plaintext.
            assert api_token.verify(session, first.plaintext).token_hash == first.hash  # type: ignore[union-attr]
            assert api_token.verify(session, second.plaintext).token_hash == second.hash  # type: ignore[union-attr]

    def test_an_unknown_token_does_not_verify(self, factory: sessionmaker[DbSession]) -> None:
        with factory() as session:
            assert api_token.verify(session, "tk_" + "de" * 20) is None

    def test_a_short_token_is_refused(self, factory: sessionmaker[DbSession]) -> None:
        """⚠️ Not load-bearing, and labelled so rather than left to look like it is.

        Removing the length guard leaves this passing: a short string still
        produces a last-eight that matches no row, so it is refused anyway. The
        guard mirrors upstream's own defence against slicing a short string and
        is kept for fidelity, not because anything here depends on it. Making it
        bite would need a row whose ``token_last_eight`` is under eight
        characters, which no minted token can produce.
        """
        with factory() as session:
            assert api_token.verify(session, "tk_123") is None

    def test_an_expired_token_does_not_verify(self, factory: sessionmaker[DbSession]) -> None:
        minted = api_token.mint()
        with factory() as session:
            session.add(_row(minted, expires_at=datetime.now(UTC) - timedelta(days=1)))
            session.commit()

            assert api_token.verify(session, minted.plaintext) is None

    def test_a_token_expiring_later_still_verifies(self, factory: sessionmaker[DbSession]) -> None:
        """The other half of the boundary: without it, "expired" could be
        returning None for every token and the test above would still pass."""
        minted = api_token.mint()
        with factory() as session:
            session.add(_row(minted, expires_at=datetime.now(UTC) + timedelta(days=1)))
            session.commit()

            assert api_token.verify(session, minted.plaintext) is not None


class TestAcceptanceMatrix:
    """Replays the recorded (grant, endpoint) table against Calton."""

    #: Endpoints Phase 1 actually serves. The recorded matrix also covers
    #: /projects, /tasks and /labels, which land with other tasks; those cells are
    #: skipped rather than asserted against a route that does not exist yet.
    IMPLEMENTED: ClassVar[set[str]] = {
        "GET /api/v1/user",
        "GET /api/v1/users",
        "GET /api/v1/routes",
        "GET /api/v1/tokens",
        "GET /api/v1/token/test",
        "POST /api/v1/user/logout",
    }

    GRANTS: ClassVar[dict[str, dict[str, list[str]]]] = {
        "other_user_only": {"other": ["user"]},
        "other_users_only": {"other": ["users"]},
        "other_routes_only": {"other": ["routes"]},
        "other_logout_only": {"other": ["logout"]},
        "tasks_read_all_only": {"tasks": ["read_all"]},
    }

    def test_the_matrix_is_not_uniform(self, go: dict[str, Any]) -> None:
        """Guards the guard. A matrix that was all-401 would assert nothing, and
        an implementation that refused everything would pass it."""
        cells = {
            status
            for grant in go["acceptance_matrix"].values()
            for endpoint, status in grant.items()
            if endpoint in self.IMPLEMENTED
        }

        assert cells == {200, 401}

    @pytest.mark.parametrize("grant", list(GRANTS))
    def test_every_recorded_cell_matches(
        self, client: TestClient, jwt: str, go: dict[str, Any], grant: str
    ) -> None:
        token = mint(client, jwt, self.GRANTS[grant])

        for endpoint, expected in go["acceptance_matrix"][grant].items():
            if endpoint not in self.IMPLEMENTED:
                continue
            method, path = endpoint.split(" ", 1)
            response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})

            assert response.status_code == expected, f"{grant}: {endpoint}"

    def test_users_is_reachable_with_other_users_granted(
        self, client: TestClient, jwt: str
    ) -> None:
        """⚠️ Contradicts the design, which lists /users as JWT-only.

        It is reachable by an API token that grants ``other.users``. The belief
        came from probing with a token that had only resource groups, which is
        refused for a different reason.
        """
        token = mint(client, jwt, {"other": ["users"]})

        response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200

    def test_a_grant_unlocks_only_its_own_subkey(self, client: TestClient, jwt: str) -> None:
        """other.user reaches /user and not /users, and vice versa."""
        user_token = mint(client, jwt, {"other": ["user"]})
        users_token = mint(client, jwt, {"other": ["users"]})

        def status(token: str, path: str) -> int:
            return client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code

        assert status(user_token, "/api/v1/user") == 200
        assert status(user_token, "/api/v1/users") == 401
        assert status(users_token, "/api/v1/users") == 200
        assert status(users_token, "/api/v1/user") == 401


class TestJwtOnlyGate:
    def test_the_registry_refuses_these_paths_on_its_own(self) -> None:
        """Where the refusal actually comes from.

        ⚠️ ``JWT_ONLY_PATHS`` is **defence in depth, not the primary guard**:
        removing it leaves both endpoints refused anyway, because the registry
        excludes the ``tokens`` group and the ``user_`` prefix outright. Asserting
        the registry directly is what pins the layer that is doing the work — a
        change there would otherwise open both endpoints with only the explicit
        list, which nobody would think to re-check, standing in the way.
        """
        from calton.core.route_registry import registry

        assert registry.lookup("GET", "/api/v1/tokens") is None
        assert registry.lookup("DELETE", "/api/v1/tokens/{tokenID}") is None
        assert registry.lookup("POST", "/api/v1/user/logout") is None

    def test_tokens_is_unreachable_by_any_api_token(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        """The gate that matters: otherwise one leaked token mints more.

        Granted every group the registry knows, and still refused.
        """
        from calton.core.route_registry import registry

        everything = {group: list(actions) for group, actions in registry.to_json().items()}
        token = mint(client, jwt, everything)

        response = client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert go["acceptance_matrix"]["other_user_only"]["GET /api/v1/tokens"] == 401

    def test_logout_is_unreachable_by_any_api_token(self, client: TestClient, jwt: str) -> None:
        """Even granted other.logout, which upstream lists but never honours."""
        token = mint(client, jwt, {"other": ["logout", "user", "users", "routes"]})

        response = client.post("/api/v1/user/logout", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401

    def test_the_same_routes_work_with_a_jwt(self, client: TestClient, jwt: str) -> None:
        """The control. Without it, the two tests above would pass against an
        implementation that had simply broken both endpoints."""
        headers = {"Authorization": f"Bearer {jwt}"}

        assert client.get("/api/v1/tokens", headers=headers).status_code == 200
        assert client.post("/api/v1/user/logout", headers=headers).status_code == 200


class TestScopeRejection:
    @pytest.mark.parametrize("case", ["wrong_group", "short_token", "unknown_token"])
    def test_rejections_use_the_invalid_token_body(
        self, client: TestClient, jwt: str, go: dict[str, Any], case: str
    ) -> None:
        """⚠️ A permission mismatch is 401 code 11, **not 403**.

        This is the single most likely thing to be implemented "correctly" and be
        wrong: a valid token used where it was not granted reads as a permission
        failure. Upstream sends it out the invalid-token exit.
        """
        narrow = mint(client, jwt, {"tasks": ["read_all"]})
        credential = {
            "wrong_group": narrow,
            "short_token": "tk_123",
            "unknown_token": "tk_" + "de" * 20,
        }[case]

        response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {credential}"})

        assert response.status_code == go["rejections"][case]["status"] == 401
        assert response.json() == go["rejections"][case]["body"]
        assert response.json()["code"] == 11

    def test_an_unregistered_route_refuses_every_token(
        self, client: TestClient, jwt: str, app: FastAPI
    ) -> None:
        """Fail closed. A route nobody registered must not be open to all tokens."""

        @app.get("/api/v1/_unregistered")
        def unregistered(subject: CurrentSubject) -> dict[str, str]:
            return {"reached": "yes"}

        token = mint(client, jwt, {"other": ["user", "users"], "tasks": ["read_all"]})

        response = client.get("/api/v1/_unregistered", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["code"] == 11

    def test_the_same_unregistered_route_works_with_a_jwt(
        self, client: TestClient, jwt: str, app: FastAPI
    ) -> None:
        """The control: the route itself is fine, it is the token that is refused."""

        @app.get("/api/v1/_unregistered_control")
        def unregistered(subject: CurrentSubject) -> dict[str, str]:
            return {"reached": "yes"}

        response = client.get(
            "/api/v1/_unregistered_control", headers={"Authorization": f"Bearer {jwt}"}
        )

        assert response.status_code == 200


class TestRouteTemplate:
    """``route_template`` is what feeds the permission lookup; get it wrong and
    tokens break in ways that point nowhere near the cause."""

    def test_it_keeps_path_parameters_as_placeholders(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """⚠️ The GHSA-v479 shape. Returning the concrete URL would derive the
        group ``tokens_5`` from ``/api/v1/tokens/5`` — registered nowhere, so
        every parameterised route refuses every token, and the obvious "fix" is
        to loosen the match, which is the vulnerability.
        """
        seen: list[str | None] = []

        @app.get("/api/v1/_probe/{thing}")
        def probe(thing: int, request: Request) -> dict[str, str]:
            seen.append(route_template(request))
            return {"ok": "yes"}

        client.get("/api/v1/_probe/5")

        assert seen == ["/api/v1/_probe/{thing}"]

    def test_it_includes_the_router_prefix(self, app: FastAPI, client: TestClient) -> None:
        """``scope["route"].path_format`` is relative to the include prefix, so the
        bare value misses every registration and API tokens look wholly broken."""
        seen: list[str | None] = []

        @app.get("/api/v1/_probe_prefix")
        def probe(request: Request) -> dict[str, str]:
            seen.append(route_template(request))
            return {"ok": "yes"}

        client.get("/api/v1/_probe_prefix")

        assert seen == ["/api/v1/_probe_prefix"]
        assert seen != ["/_probe_prefix"]


class TestTokenOnAParameterisedRoute:
    """A token reaching a route that has a path parameter.

    Every other token test here hits a static path, where the concrete URL and
    the template happen to be identical — so none of them can tell the two apart.
    This one stands in a route shaped like the real ``/labels/{label}`` so the
    difference is observable: feeding the lookup ``request.url.path`` derives the
    group ``teams_5`` and refuses a correctly-granted token.

    ⚠️ The probe used to be mounted at ``/api/v1/labels/{label}`` itself. Once T25
    mounted the real labels routes, FastAPI matched the real route first — it is
    registered earlier — and the real policy answered 403 for a label that does not
    exist. The test then read as "a granted token was refused", which is this very
    class's failure mode, on a request that never reached the code under test.

    A probe path must not collide with a mounted route, *and* its first segment
    must be one of the registry's known resource groups — an unknown one lands
    under ``other`` with the segment as the action name, so the grant matches
    nothing and the test fails 401 for a reason that has nothing to do with path
    templates.

    ⚠️ **This has now moved twice for the same reason**, and the second time was
    predicted in this docstring and still cost a debugging round. It was
    ``/labels/{label}`` until T25 mounted labels; it was then ``/teams/{team}``
    with a note saying "when teams are implemented, move this again", and Phase
    2 mounted teams. Both times the symptom was identical and misleading: a
    correctly granted token answering 403, which is precisely this class's own
    failure mode, on a request that never reached the code under test.

    So the note is no longer the guard — :meth:`test_the_probe_path_is_not_a_real
    _route` is. It fails by name the moment someone mounts the probe's path,
    instead of leaving the next person to rediscover this from a 403. Moving the
    probe then takes a minute rather than an afternoon.

    ``time_entries`` is the current choice because it is in the registry's
    resource groups and is in no phase's whitelist, so nothing is due to mount it.
    """

    PROBE_PATH = "/api/v1/time_entries/{entry}"
    PROBE_URL = "/api/v1/time_entries/5"
    PROBE_GROUP = "time_entries"

    def test_the_probe_path_is_not_a_real_route(self) -> None:
        """The premise every other test in this class rests on.

        If ``create_app`` mounts this path, FastAPI matches the real route first and
        the real policy answers whatever it answers — and the two tests below then
        report "a granted token was refused", which is a conclusion about the token
        check drawn from a request that never reached it.

        Read from ``openapi()["paths"]`` rather than ``app.routes``: included routers
        are wrapped in objects with no ``.path``, so scanning ``app.routes`` sees
        nothing and this guard would pass vacuously — the exact shape it exists to
        prevent.
        """
        from calton.main import create_app

        assert self.PROBE_PATH not in create_app().openapi()["paths"], (
            f"{self.PROBE_PATH} is now a real route, so this class's probe collides "
            f"with it. Move the probe to another path whose first segment is one of "
            f"route_registry.CRUD_RESOURCES and which create_app does not mount."
        )

    @pytest.fixture
    def app_with_parameterised_route(self, app: FastAPI) -> FastAPI:
        from calton.core.route_registry import registry

        registry.register("GET", self.PROBE_PATH)

        @app.get(self.PROBE_PATH)
        def read_probe(entry: int, subject: CurrentSubject) -> dict[str, int]:
            return {"id": entry}

        return app

    def test_a_granted_token_reaches_it(
        self, app_with_parameterised_route: FastAPI, client: TestClient, jwt: str
    ) -> None:
        token = mint(client, jwt, {self.PROBE_GROUP: ["read_one"]})

        response = client.get(self.PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200

    def test_a_token_without_that_action_is_refused(
        self, app_with_parameterised_route: FastAPI, client: TestClient, jwt: str
    ) -> None:
        """The control: read_all does not imply read_one."""
        token = mint(client, jwt, {self.PROBE_GROUP: ["read_all"]})

        response = client.get(self.PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401


class TestTokenEndpoints:
    def test_creation_returns_201_and_the_plaintext_once(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        created = client.put(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "title": "mine",
                "permissions": {"tasks": ["read_all"]},
                "expires_at": "2027-01-01T00:00:00Z",
            },
        )

        assert created.status_code == go["create"]["status"] == 201
        assert sorted(created.json()) == go["create"]["body_keys"]
        assert created.json()["token"].startswith("tk_")

    def test_the_plaintext_is_never_listed(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        """Only the digest is stored, so listing could not show it even if it
        tried — this pins that nobody adds it back by widening the schema."""
        mint(client, jwt, {"tasks": ["read_all"]})

        listed = client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {jwt}"})

        assert listed.status_code == 200
        assert listed.json() != []
        assert all("token" not in entry for entry in listed.json())
        assert go["list"]["plaintext_present"] is False

    def test_the_list_is_paginated_unlike_users(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        """The contrast worth pinning: /tokens goes through the generic handler
        and /users does not, so they differ on headers and on empty bodies."""
        listed = client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {jwt}"})

        for header in ("x-pagination-result-count", "x-pagination-total-pages"):
            assert header in listed.headers
            assert header in go["list"]["header_names"]
        assert "access-control-expose-headers" in listed.headers

    def test_an_empty_list_is_a_list_not_null(self, client: TestClient, jwt: str) -> None:
        listed = client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {jwt}"})

        assert listed.json() == []
        assert listed.text.strip() != "null"

    def test_deletion_reports_success(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        created = client.put(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                # Not `{}`. Measured: upstream answers 412 `permissions: non zero
                # value required` to an empty map exactly as it does to an absent
                # one — a token granting nothing is not something it will mint.
                # This test wanted a token to delete, not a permission-less one,
                # so it takes the narrowest grant that actually exists.
                "title": "doomed",
                "permissions": {"tasks": ["read_all"]},
                "expires_at": "2027-01-01T00:00:00Z",
            },
        )
        assert created.status_code == 201, created.text
        token_id = created.json()["id"]

        deleted = client.delete(
            f"/api/v1/tokens/{token_id}", headers={"Authorization": f"Bearer {jwt}"}
        )

        assert deleted.status_code == go["delete"]["status"] == 200
        assert deleted.json() == go["delete"]["body"]

    def test_a_deleted_token_stops_authenticating(self, client: TestClient, jwt: str) -> None:
        created = client.put(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "title": "doomed",
                "permissions": {"other": ["user"]},
                "expires_at": "2027-01-01T00:00:00Z",
            },
        )
        plaintext = created.json()["token"]
        headers = {"Authorization": f"Bearer {plaintext}"}
        assert client.get("/api/v1/user", headers=headers).status_code == 200

        client.delete(
            f"/api/v1/tokens/{created.json()['id']}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        assert client.get("/api/v1/user", headers=headers).status_code == 401

    def test_deleting_an_unknown_id_is_403_not_404(
        self, client: TestClient, jwt: str, go: dict[str, Any]
    ) -> None:
        """⚠️ 403 with the generic write-denied body, not 404.

        404 would both diverge and disclose which token ids exist.
        """
        response = client.delete(
            "/api/v1/tokens/999999", headers={"Authorization": f"Bearer {jwt}"}
        )

        assert response.status_code == go["delete_missing"]["status"] == 403
        assert response.json() == go["delete_missing"]["body"]

    def test_a_token_belonging_to_someone_else_cannot_be_deleted(
        self, client: TestClient, jwt: str, factory: sessionmaker[DbSession]
    ) -> None:
        minted = api_token.mint()
        with factory() as session:
            row = _row(minted)
            row.owner_id = 999
            session.add(row)
            session.commit()
            other_id = row.id

        response = client.delete(
            f"/api/v1/tokens/{other_id}", headers={"Authorization": f"Bearer {jwt}"}
        )

        assert response.status_code == 403

    def test_listing_shows_only_your_own_tokens(
        self, client: TestClient, jwt: str, factory: sessionmaker[DbSession]
    ) -> None:
        minted = api_token.mint()
        with factory() as session:
            row = _row(minted)
            row.owner_id = 999
            session.add(row)
            session.commit()

        listed = client.get("/api/v1/tokens", headers={"Authorization": f"Bearer {jwt}"})

        assert listed.json() == []


class TestWiring:
    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/tokens", "get"),
            ("/api/v1/tokens", "put"),
            ("/api/v1/tokens/{tokenID}", "delete"),
            ("/api/v1/routes", "get"),
        ],
    )
    def test_route_is_in_the_generated_contract(self, app: FastAPI, path: str, method: str) -> None:
        assert method in app.openapi()["paths"].get(path, {}), path

    def test_routes_requires_authentication(self, client: TestClient) -> None:
        """Anonymous access publishes the whole token permission vocabulary."""
        response = client.get("/api/v1/routes")

        assert response.status_code == 401
        assert response.json()["code"] == 11


class TestTheAuthChainIsWired:
    """A valid credential must actually reach the handler.

    This is the project's fourth "module delivered, never connected" failure — the
    error handlers, the SPA fallback and the endpoint routing were the first three.
    It is worth its own class because the failure is invisible to every other test
    here: the resource lines read the subject off ``request.state.auth`` and their
    own suites supply it from a stub middleware, so they pass whether or not
    anything populates it in the real app. Measured against the reference server
    before the fix: ``GET /api/v1/user`` 200 and ``GET /api/v1/tasks`` 401 on one
    and the same JWT.

    These use the module's own ``client``, which has no stub and no dependency
    override, so the credential travels the production path.
    """

    def test_a_real_jwt_reaches_a_task_route(self, client: TestClient, jwt: str) -> None:
        """The regression itself: 401 here means state.auth was never populated."""
        response = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {jwt}"})

        assert response.status_code != 401, (
            "a valid JWT was refused by a task route — request.state.auth is not "
            "being populated, so the auth line and the resource lines are not connected"
        )
        assert response.status_code == 200

    def test_the_same_route_is_still_closed_to_anonymous_callers(self, client: TestClient) -> None:
        """The other direction, so the fix cannot be "let everyone through"."""
        response = client.get("/api/v1/tasks")

        assert response.status_code == 401
        assert response.json()["code"] == 11

    def test_the_public_endpoint_stays_public_and_the_gated_one_stays_gated(
        self, client: TestClient
    ) -> None:
        """One assertion, both directions of the wiring's blast radius.

        ``/info`` is the only anonymous endpoint upstream serves, and ``/routes`` sits
        next to it as metadata that does require a caller. Attaching the resolver too
        widely — at the app rather than at a router — breaks the first; attaching it
        too narrowly, or not at all, breaks the second. Pinning the pair catches both
        mistakes, and neither is visible from a test of one endpoint alone.
        """
        assert client.get("/api/v1/info").status_code == 200
        assert client.get("/api/v1/routes").status_code == 401

    def test_an_api_token_granted_the_group_reaches_a_task_route(
        self, client: TestClient, jwt: str
    ) -> None:
        """The token path resolves the route template too.

        Distinct from the JWT case: this one goes through ``route_template``, which
        reads ``scope["route"]``. Resolving the subject in an HTTP middleware would
        pass the JWT test above and fail this one, because Starlette has not routed
        the request yet when a middleware's pre-``call_next`` half runs.
        """
        token = mint(client, jwt, {"tasks": ["read_all"]})

        response = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200

    def test_every_registered_route_answers_401_to_an_anonymous_caller(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """The guard against the next unwired router.

        Walks what ``create_app`` registered rather than a hand-kept list, so a
        resource router mounted without the auth dependency fails here instead of
        being found by the parity harness weeks later. ``/token/test`` is excluded:
        it validates a credential rather than authorising one, and upstream answers
        418 to an anonymous call.

        ⚠️ Parameterised paths used to be skipped outright, which left the sweep
        blind to **every item route in the API** — each of `/tasks/{task}`,
        `/labels/{label}`, the assignee, comment and relation routes and the rest.
        The one thing this test exists to catch is a router mounted without
        ``dependencies=[Depends(get_auth_subject)]``, and that is a property of the
        *router*, so a resource whose only unparameterised path happened to be
        mounted elsewhere could have gone unnoticed entirely. Placeholders are
        filled with "1" instead: authentication runs before routing resolves any id,
        so whether the id exists cannot change a 401 into anything else.
        """
        import re

        from calton.core.route_registry import registry

        # ``registry`` is a module-level singleton, so other test modules' fixtures leave
        # entries in it for routes this app never mounted (``/api/v1/teams/{team}`` among
        # them). Intersecting with what the app actually serves keeps the sweep about the
        # thing it is testing; "declared in REGISTERED_ROUTES but not mounted" is a
        # different failure and each resource's wiring file asserts it directly.
        mounted = app.openapi()["paths"]

        unprotected = []
        for method, path in sorted(registry.paths()):
            if path in ("/api/v1/token/test",):
                continue
            if method.lower() not in mounted.get(path, {}):
                continue
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            response = client.request(method, concrete)
            if response.status_code != 401:
                unprotected.append(f"{method} {path} -> {response.status_code}")

        assert not unprotected, (
            "these registered routes did not refuse an anonymous caller, which "
            "means they are mounted without the auth dependency: " + "; ".join(unprotected)
        )


def _minted_ending_in(body: str, suffix: str) -> api_token.MintedToken:
    """A token with a chosen plaintext, so two can share a last-eight on purpose."""
    import secrets

    plaintext = f"{api_token.TOKEN_PREFIX}{body}{suffix}"
    salt = "".join(secrets.choice(api_token.SALT_ALPHABET) for _ in range(api_token.SALT_LENGTH))
    return api_token.MintedToken(
        plaintext=plaintext,
        salt=salt,
        hash=api_token.hash_token(plaintext, salt),
        last_eight=plaintext[-8:],
    )


def _row(minted: api_token.MintedToken, expires_at: datetime | None = None) -> APIToken:
    return APIToken(
        title="probe",
        token_salt=minted.salt,
        token_hash=minted.hash,
        token_last_eight=minted.last_eight,
        permissions="{}",
        expires_at=expires_at or datetime(2027, 1, 1, tzinfo=UTC),
        owner_id=900,
    )


# --------------------------------------------------------------------------------------
# Wire key order. Both cases below assert on the **bytes**, never on the parsed body:
# `model_dump()` and `response.json()` are dicts, and dict equality ignores order, so a
# test written that way passes against both the right answer and the wrong one. That is
# not hypothetical — it is why these two defects survived a green suite.
# --------------------------------------------------------------------------------------


class TestTheCreatedTokenKeyOrder:
    """★ `token` is **third**, not last: id, title, token, permissions, … (measured).

    A subclass cannot produce this. Pydantic orders inherited fields before new ones,
    so `class CreatedTokenResponse(TokenResponse)` emits `token` last — and rewriting
    every field inside the subclass does not move it either, because inherited names
    keep the parent's position. Only a flat model works.
    """

    def test_the_created_response_puts_token_third(self) -> None:
        from calton.api.v1.tokens import CreatedTokenResponse

        assert list(CreatedTokenResponse.model_fields) == [
            "id",
            "title",
            "token",
            "permissions",
            "expires_at",
            "created",
            "owner_id",
        ]

    def test_created_and_listed_are_not_related_by_inheritance(self) -> None:
        """The structural fact the field order depends on.

        Asserting the order alone would go red with a confusing message the day someone
        "tidies up the duplication" by reintroducing the base class. This says what the
        constraint actually is, so the failure names the cause.
        """
        from calton.api.v1.tokens import CreatedTokenResponse, TokenResponse

        assert not issubclass(CreatedTokenResponse, TokenResponse), (
            "CreatedTokenResponse must stay flat. Inheriting puts `token` last on the "
            "wire, and re-declaring the fields in the subclass does not fix it — "
            "Pydantic keeps a parent's position for inherited names."
        )

    def test_dict_comparison_cannot_see_this(self) -> None:
        """Why the two tests above assert on field order rather than on a body.

        Pinning the guard's own blind spot: if a later reviewer replaces them with the
        natural `assert response.json() == {...}`, this case says plainly that such an
        assertion is satisfied by the wrong order too.
        """
        from calton.api.v1.tokens import CreatedTokenResponse

        epoch = datetime(2026, 1, 1, tzinfo=UTC)
        built = CreatedTokenResponse(
            id=1,
            title="t",
            permissions={},
            expires_at=epoch,
            created=epoch,
            owner_id=2,
            token="tk_x",
        )
        # The same seven pairs, written in the order a subclass would emit them.
        wrong_order: dict[str, Any] = {
            "id": 1,
            "title": "t",
            "permissions": {},
            "expires_at": epoch,
            "created": epoch,
            "owner_id": 2,
            "token": "tk_x",
        }

        # Equal as mappings...
        assert built.model_dump() == wrong_order
        # ...and different as sequences, which is the only view the wire has.
        assert list(built.model_dump()) != list(wrong_order)
        assert list(built.model_dump()).index("token") == 2
        assert list(wrong_order).index("token") == 6


class TestPermissionsAreSortedBecauseUpstreamIsAMap:
    """★ Group keys come back alphabetical, not in the order they were sent.

    Upstream's `permissions` is a Go `map[string][]string` and `encoding/json` sorts map
    keys, so this is a property of the serialiser rather than of a declaration order —
    which is why the fix is a sort and not a fixed sequence. A pinned order would agree
    with upstream only while the groups in play happened to be alphabetical.
    """

    def test_group_keys_are_sorted(self) -> None:
        from calton.api.v1.tokens import _sorted_permissions

        sent = {"tasks": ["read_all"], "projects": ["read_all"], "labels": ["read_all"]}

        assert list(_sorted_permissions(sent)) == ["labels", "projects", "tasks"]

    def test_the_sample_distinguishes_sorting_from_insertion_order(self) -> None:
        """Without this, an implementation that just echoes the input passes.

        The sample has to disagree with itself sorted, or both hypotheses fit it. This
        is the same reason the reference measurement was taken with three groups written
        out of alphabetical order.
        """
        sent = {"tasks": ["read_all"], "projects": ["read_all"], "labels": ["read_all"]}

        assert list(sent) != sorted(sent)

    def test_the_action_lists_are_left_alone(self) -> None:
        """Only the map keys move. The values are JSON arrays on both sides, and array
        order is contractual — sorting them too is the tempting over-correction."""
        from calton.api.v1.tokens import _sorted_permissions

        sent = {"tasks": ["update", "create", "read_all"]}

        assert _sorted_permissions(sent)["tasks"] == ["update", "create", "read_all"]
