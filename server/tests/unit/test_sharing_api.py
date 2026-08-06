"""The three sharing creates, over HTTP through ``create_app``.

===  =========================================================================
950  P-ALICE, alice's. bob has write (not admin), carol has admin.
951  P-CAROL, carol's — alice cannot see it.
960  T-ONE, a team. dave is a user with no grants anywhere.
===  =========================================================================

bob and carol are both non-owners and are **not** interchangeable: these routes need
admin, and bob's write is what proves it. A fixture with only an owner and a stranger
cannot tell "admin" from "any collaborator".
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from calton.auth.deps import get_auth_subject
from calton.db.base import Base
from calton.db.session import session_factory
from calton.main import create_app
from calton.models import LinkShare, Project, ProjectUser, Team, TeamProject, User

ALICE, BOB, CAROL, DAVE = 900, 901, 902, 903
P_ALICE, P_CAROL = 950, 951
T_ONE = 960
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    built = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(built)
    yield built
    built.dispose()


def _seed(session: Session) -> None:
    session.add_all(
        [
            User(id=ALICE, username="alice", created=EPOCH, updated=EPOCH),
            User(id=BOB, username="bob", created=EPOCH, updated=EPOCH),
            User(id=CAROL, username="carol", created=EPOCH, updated=EPOCH),
            User(id=DAVE, username="dave", created=EPOCH, updated=EPOCH),
        ]
    )
    session.add_all(
        [
            Project(id=P_ALICE, title="P-ALICE", identifier="", owner_id=ALICE, position=1),
            Project(id=P_CAROL, title="P-CAROL", identifier="", owner_id=CAROL, position=2),
        ]
    )
    session.add_all(
        [
            ProjectUser(project_id=P_ALICE, user_id=BOB, permission=1),
            ProjectUser(project_id=P_ALICE, user_id=CAROL, permission=2),
        ]
    )
    session.add(Team(id=T_ONE, name="T-ONE", created_by_id=ALICE, created=EPOCH, updated=EPOCH))
    session.commit()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    factory = session_factory(engine)
    with factory() as session:
        _seed(session)
    return factory


@pytest.fixture
def session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as opened:
        yield opened


@pytest.fixture
def app(engine: Engine, sessions: sessionmaker[Session]) -> FastAPI:
    application = create_app(engine=engine)
    application.state.session_factory = sessions

    @application.middleware("http")
    async def _stub_auth(request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("x-test-user")
        if header:
            # ⚠️ `timestamps_are_zero` is part of the subject, not decoration: the
            # link-share response embeds the authenticated subject, and a JWT subject
            # carries no timestamps while an API-token one does. A stub that omitted it
            # would make every test here look like the token case and could never see the
            # difference. `X-Test-Credential: jwt` selects the other one.
            request.state.auth = SimpleNamespace(
                id=int(header),
                timestamps_are_zero=request.headers.get("x-test-credential") == "jwt",
            )
        return await call_next(request)

    application.dependency_overrides[get_auth_subject] = lambda: None
    return application


def as_user(app: FastAPI, user_id: int) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(user_id)}, raise_server_exceptions=False)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


class TestSharingWithAUser:
    def test_the_owner_grants_by_username(self, client: TestClient, session: Session) -> None:
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave", "permission": 1}
        )

        assert resp.status_code == 201
        assert resp.json()["username"] == "dave"
        assert resp.json()["permission"] == 1
        session.expire_all()
        row = session.query(ProjectUser).filter(ProjectUser.user_id == DAVE).one_or_none()
        assert row is not None and row.permission == 1

    def test_the_response_id_is_the_relation_not_the_user(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ The create answers the **relation row's** id; the sibling GET answers the
        **user's**, under the same key, on the same path. dave's user id is 903 and no
        relation row will be numbered that, so returning the wrong one is visible."""
        body = client.put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave"}).json()
        session.expire_all()

        row = session.query(ProjectUser).filter(ProjectUser.user_id == DAVE).one()
        assert body["id"] == row.id
        assert body["id"] != DAVE

    def test_an_admin_who_is_not_the_owner_may_grant(self, app: FastAPI) -> None:
        assert (
            as_user(app, CAROL)
            .put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave"})
            .status_code
            == 201
        )

    def test_write_permission_is_not_enough(self, app: FastAPI, session: Session) -> None:
        """bob has write. The pair with the case above is what makes this "admin" rather
        than "any collaborator"."""
        resp = as_user(app, BOB).put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave"})

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}
        session.expire_all()
        assert session.query(ProjectUser).filter(ProjectUser.user_id == DAVE).one_or_none() is None

    def test_a_duplicate_is_409_7002(self, client: TestClient) -> None:
        client.put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave"})
        resp = client.put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave"})

        assert resp.status_code == 409
        assert resp.json()["code"] == 7002

    def test_granting_the_owner_is_also_409(self, client: TestClient) -> None:
        """The owner already has access by owning it — measured, and not the obvious
        answer: there is no relation row to collide with."""
        resp = client.put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "alice"})

        assert resp.status_code == 409
        assert resp.json()["code"] == 7002

    def test_an_unknown_username_and_an_empty_body_are_the_same_404(
        self, client: TestClient
    ) -> None:
        """⚠️ 404/1005 for both, **not** a 412. The user lookup runs on the zero value
        before anything validates, so "no such user" and "no username at all" are one
        answer. Marking the field required is the natural reading and emits a status this
        route never sends."""
        unknown = client.put(f"/api/v1/projects/{P_ALICE}/users", json={"username": "nosuch"})
        empty = client.put(f"/api/v1/projects/{P_ALICE}/users", json={})

        assert unknown.status_code == empty.status_code == 404
        assert unknown.json() == empty.json()
        assert unknown.json()["code"] == 1005

    def test_an_out_of_range_permission_is_400(self, client: TestClient) -> None:
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/users", json={"username": "dave", "permission": 9}
        )

        assert resp.status_code == 400
        assert resp.json()["code"] == 2004

    def test_a_missing_project_is_404_3001(self, client: TestClient) -> None:
        resp = client.put("/api/v1/projects/99999/users", json={"username": "dave"})

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_the_project_is_looked_up_before_rights(self, app: FastAPI) -> None:
        """A project that does not exist answers 3001 even to someone who could never
        have administered it — existence beats permission here."""
        resp = as_user(app, DAVE).put("/api/v1/projects/99999/users", json={"username": "bob"})

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001


class TestSharingWithATeam:
    def test_the_owner_grants_a_team(self, client: TestClient, session: Session) -> None:
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": T_ONE, "permission": 1}
        )

        assert resp.status_code == 201
        assert resp.json()["team_id"] == T_ONE
        assert resp.json()["permission"] == 1
        session.expire_all()
        assert session.query(TeamProject).filter(TeamProject.team_id == T_ONE).one() is not None

    def test_right_is_ignored_and_permission_is_not(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ Three arms, because two cannot tell the candidate rules apart.

        Sending ``{"right": 1}`` answers ``permission: 0``. That is equally consistent
        with "``right`` is ignored" and with "``permission`` cannot be set on this route
        at all" — and I carried the two-arm version as a stated conclusion for several
        rounds before noticing it was an interpretation rather than an observation.

            A  {"team_id": T, "permission": 1}  -> 1   rules out "unsettable"
            B  {"team_id": T, "right": 1}       -> 0
            C  {"team_id": T}                   -> 0   B == C is what makes `right` ignored

        B and C agreeing is the load-bearing comparison; A alone or B alone proves
        nothing about the other reading.
        """
        session.add_all(
            [
                Team(id=961, name="T-TWO", created_by_id=ALICE, created=EPOCH, updated=EPOCH),
                Team(id=962, name="T-THREE", created_by_id=ALICE, created=EPOCH, updated=EPOCH),
            ]
        )
        session.commit()

        arm_a = client.put(
            f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": T_ONE, "permission": 1}
        )
        arm_b = client.put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": 961, "right": 1})
        arm_c = client.put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": 962})

        assert arm_a.json()["permission"] == 1, "permission is settable"
        assert arm_b.json()["permission"] == arm_c.json()["permission"] == 0, "right is ignored"

    def test_a_duplicate_is_409_6004(self, client: TestClient) -> None:
        client.put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": T_ONE})
        resp = client.put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": T_ONE})

        assert resp.status_code == 409
        assert resp.json()["code"] == 6004

    def test_an_unknown_team_and_an_empty_body_are_the_same_404(self, client: TestClient) -> None:
        """6002 for both — team 0 does not exist either, so the empty body takes the same
        branch. Different code from the user route's 1005, same shape of surprise."""
        unknown = client.put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": 99999})
        empty = client.put(f"/api/v1/projects/{P_ALICE}/teams", json={})

        assert unknown.status_code == empty.status_code == 404
        assert unknown.json()["code"] == empty.json()["code"] == 6002

    def test_write_permission_is_not_enough(self, app: FastAPI) -> None:
        resp = as_user(app, BOB).put(f"/api/v1/projects/{P_ALICE}/teams", json={"team_id": T_ONE})

        assert resp.status_code == 403


class TestCreatingALinkShare:
    def test_the_owner_creates_one(self, client: TestClient, session: Session) -> None:
        resp = client.put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0})

        assert resp.status_code == 201
        assert len(resp.json()["hash"]) == 40
        session.expire_all()
        assert session.query(LinkShare).filter(LinkShare.project_id == P_ALICE).one() is not None

    def test_sharing_type_is_derived_from_the_password(self, client: TestClient) -> None:
        """⚠️ 1 without a password, 2 with one — and it is **not** taken from the body.

        A client that sends its own ``sharing_type`` has it ignored; trusting the body
        would let a caller advertise a password-protected link that has no password.
        """
        plain = client.put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0})
        secured = client.put(
            f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0, "password": "pw"}
        )
        lying = client.put(
            f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0, "sharing_type": 2}
        )

        assert plain.json()["sharing_type"] == 1
        assert secured.json()["sharing_type"] == 2
        assert lying.json()["sharing_type"] == 1

    def test_the_password_is_never_returned(self, client: TestClient, session: Session) -> None:
        """Stored, masked on the way out. The row is checked separately so "masked" is not
        confused with "discarded" — the same split webhooks needed."""
        body = client.put(
            f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0, "password": "pw"}
        ).json()

        assert body["password"] == ""
        session.expire_all()
        row = session.get(LinkShare, body["id"])
        assert row is not None and row.password == "pw"

    def test_shared_by_timestamps_depend_on_the_credential(self, app: FastAPI) -> None:
        """⚠️ The embedded subject is the **authenticated subject**, so one field of this
        response depends on how the caller logged in.

        A JWT subject is built from claims and carries no timestamps; an API-token subject
        is loaded from the database and carries real ones. Both measured.

        ⚠️ **Invisible under an API token alone**: "echo the subject" and "read the row"
        answer identically there, so a suite that only authenticates with tokens certifies
        either implementation. Only the JWT arm discriminates — which is why both are here
        rather than the one that happens to match the default fixture.
        """
        jwt_client = TestClient(
            app,
            headers={"X-Test-User": str(ALICE), "X-Test-Credential": "jwt"},
            raise_server_exceptions=False,
        )
        token_client = TestClient(
            app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False
        )

        via_jwt = jwt_client.put(
            f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0}
        ).json()
        via_token = token_client.put(
            f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0}
        ).json()

        assert via_jwt["shared_by"]["id"] == via_token["shared_by"]["id"] == ALICE
        assert via_jwt["shared_by"]["username"] == "alice"
        assert via_jwt["shared_by"]["created"] == "0001-01-01T00:00:00Z"
        assert via_token["shared_by"]["created"] != "0001-01-01T00:00:00Z"

    def test_each_share_gets_a_distinct_hash(self, client: TestClient) -> None:
        """Uniquely indexed upstream. A constant or a low-entropy hash would collide on
        the second insert, and the first test would still pass."""
        first = client.put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0}).json()
        second = client.put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0}).json()

        assert first["hash"] != second["hash"]

    def test_write_is_enough_here_unlike_the_other_two(self, app: FastAPI) -> None:
        """⚠️ **The one rung that separates this route from its siblings.**

        Measured ladder, independently by coder-e and then by me on a fresh project:

            caller      PUT /shares   GET /shares   PUT /teams   PUT /users
            read  (0)       403           403           403          403
            write (1)     **201**         403           403          403
            admin (2)       201           200           201          201

        A write-level collaborator can mint a share link — a fresh credential into the
        project — while being unable to list the ones that already exist. Able to grant,
        unable to audit.

        This implementation required admin here until the ladder was run. My own earlier
        measurement only used a read-level user, which is 403 under both rules: the sample
        could not reach the row that tells them apart. Making all four admin-only is the
        natural implementation and looks *more* correct, which is why it would survive
        review.
        """
        assert (
            as_user(app, BOB).put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0})
        ).status_code == 201

    def test_but_read_permission_is_not_enough(self, app: FastAPI, session: Session) -> None:
        """The rung below. Without it, "needs write" and "needs nothing" are one rule."""
        session.add(ProjectUser(project_id=P_ALICE, user_id=DAVE, permission=0))
        session.commit()

        resp = as_user(app, DAVE).put(f"/api/v1/projects/{P_ALICE}/shares", json={"permission": 0})

        assert resp.status_code == 403

    def test_a_missing_project_is_404_3001(self, client: TestClient) -> None:
        resp = client.put("/api/v1/projects/99999/shares", json={"permission": 0})

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001


class TestTheSharingRoutesAreWired:
    def test_all_three_are_mounted(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        for path in (
            "/api/v1/projects/{project}/users",
            "/api/v1/projects/{project}/teams",
            "/api/v1/projects/{project}/shares",
        ):
            assert path in paths, path
            assert "put" in paths[path]

    def test_the_permission_groups_are_one_per_path(self) -> None:
        """Each (group, action) pair has one route, and the §8 reads file as read_all."""
        from calton.core.route_registry import registry

        create_app()
        table = registry.to_json()
        assert set(table["projects_users"]) == {"create", "read_all"}
        assert set(table["projects_teams"]) == {"create", "read_all"}
        assert set(table["projects_shares"]) == {"create", "read_all"}
