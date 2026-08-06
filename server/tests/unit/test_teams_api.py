"""The team endpoints, driven over HTTP through ``create_app``.

Everything here goes through the real application — real router, real policy, real
service, real error handlers. A service-level test cannot see any of the failures this
file exists for: a router that was never mounted, a policy that turns 404 into 403, a
member route that parses its username as an id.

The world mirrors the parity seed's team fixtures, with the same shape that makes the
permission cells distinguishable:

===  =========================================================================
910  T-ADMIN, created by alice. Members: alice (admin) and bob (plain).
911  T-PLAIN, created by alice. Members: alice (plain) — nobody administers it.
912  T-OTHER, created by carol. Members: carol (admin) only; alice is not in it.
===  =========================================================================

Those three teams are not decoration. ``T-ADMIN`` and ``T-PLAIN`` differ **only** in
alice's admin flag, which is the only thing that separates "may read" from "may write";
``T-OTHER`` is the team alice cannot see at all. Without all three, a policy that ignored
the admin flag entirely would pass — the fixed point practice 4 calls the permission-set
one, where the readable set and the writable set are accidentally equal.

Likewise ``dave`` exists and is a member of nothing: he is the subject for "a real user
who is not a member", which a non-existent username cannot stand in for because the two
have different answers (200 vs 404/1005).
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
from calton.models import Project, Team, TeamMember, TeamProject, User

ALICE, BOB, CAROL, DAVE = 900, 901, 902, 903
T_ADMIN, T_PLAIN, T_OTHER = 910, 911, 912

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
            Team(
                id=T_ADMIN,
                name="T-ADMIN",
                description="keep me",
                created_by_id=ALICE,
                created=EPOCH,
                updated=EPOCH,
            ),
            Team(id=T_PLAIN, name="T-PLAIN", created_by_id=ALICE, created=EPOCH, updated=EPOCH),
            Team(id=T_OTHER, name="T-OTHER", created_by_id=CAROL, created=EPOCH, updated=EPOCH),
        ]
    )
    session.add_all(
        [
            TeamMember(team_id=T_ADMIN, user_id=ALICE, admin=True, created=EPOCH),
            TeamMember(team_id=T_ADMIN, user_id=BOB, admin=False, created=EPOCH),
            # alice is a member here but NOT an admin — the discriminating row.
            TeamMember(team_id=T_PLAIN, user_id=ALICE, admin=False, created=EPOCH),
            TeamMember(team_id=T_OTHER, user_id=CAROL, admin=True, created=EPOCH),
        ]
    )
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
            request.state.auth = SimpleNamespace(id=int(header))
        return await call_next(request)

    # ⚠️ Overriding this means these tests do NOT cover the auth wiring; that is what
    # TestTheAuthChainIsWired in test_api_tokens.py is for. It walks the registry, so the
    # team routes are covered there by being registered, not by anything in this file.
    application.dependency_overrides[get_auth_subject] = lambda: None

    return application


def as_user(app: FastAPI, user_id: int) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(user_id)}, raise_server_exceptions=False)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Authenticated as alice."""
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def stored_team(session: Session, team_id: int) -> Team:
    """The row, failing by name if it is gone."""
    stored = session.get(Team, team_id)
    assert stored is not None, f"team {team_id} is not in the database"
    return stored


def member_row(session: Session, team_id: int, user_id: int) -> TeamMember | None:
    return (
        session.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        .one_or_none()
    )


# --- reading -----------------------------------------------------------------


class TestReadingTeams:
    def test_the_collection_lists_only_teams_you_belong_to(self, client: TestClient) -> None:
        resp = client.get("/api/v1/teams")

        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()] == [T_ADMIN, T_PLAIN]

    def test_the_collection_sends_pagination_headers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/teams")

        assert resp.headers["x-pagination-result-count"] == "2"
        assert resp.headers["x-pagination-total-pages"] == "1"

    def test_the_total_counts_every_match_not_just_the_page(self, client: TestClient) -> None:
        """The count is taken before the slice. Computing it from the page would make
        total-pages 1 for every request, which no single-page assertion can see."""
        resp = client.get("/api/v1/teams?page=1&per_page=1")

        assert [t["id"] for t in resp.json()] == [T_ADMIN]
        assert resp.headers["x-pagination-result-count"] == "1"
        assert resp.headers["x-pagination-total-pages"] == "2"

    def test_the_search_term_filters_by_name(self, client: TestClient) -> None:
        # "PLAIN" rather than "T-": both teams start with T-, so a prefix would match
        # both and could not tell filtering from not filtering.
        resp = client.get("/api/v1/teams?s=PLAIN")

        assert [t["id"] for t in resp.json()] == [T_PLAIN]

    def test_a_member_reads_the_item(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/teams/{T_ADMIN}")

        assert resp.status_code == 200
        assert resp.json()["name"] == "T-ADMIN"

    def test_an_admin_member_reports_permission_two(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/teams/{T_ADMIN}")

        assert resp.headers["x-max-permission"] == "2"

    def test_a_plain_member_reports_permission_zero(self, client: TestClient) -> None:
        """Paired with the case above on purpose. A policy that returned a constant 2
        passes every status assertion in this class and only this pair separates them."""
        resp = client.get(f"/api/v1/teams/{T_PLAIN}")

        assert resp.status_code == 200
        assert resp.headers["x-max-permission"] == "0"

    def test_a_non_member_is_refused(self, app: FastAPI) -> None:
        resp = as_user(app, ALICE).get(f"/api/v1/teams/{T_OTHER}")

        assert resp.status_code == 403
        assert resp.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }

    def test_a_missing_team_is_refused_identically_on_the_read_path(
        self, client: TestClient
    ) -> None:
        """403, not 404 — and byte-identical to the case above.

        Reporting 404 here would turn this route into an oracle for which team ids exist.
        That is why the assertion compares the whole body against the previous test's,
        rather than just the status: a 403 carrying a different message would leak the
        same thing more quietly.
        """
        resp = client.get("/api/v1/teams/99999")

        assert resp.status_code == 403
        assert resp.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }

    def test_a_non_numeric_id_is_400_not_422(self, client: TestClient) -> None:
        resp = client.get("/api/v1/teams/notanint")

        assert resp.status_code == 400
        assert resp.json()["code"] == 2004

    def test_members_carry_the_admin_flag_and_are_user_ids(self, client: TestClient) -> None:
        """``members[].id`` is the **user** id. The member routes answer a membership row
        id under the same key, which is why the two have separate schemas."""
        members = client.get(f"/api/v1/teams/{T_ADMIN}").json()["members"]

        assert [(m["id"], m["username"], m["admin"]) for m in members] == [
            (ALICE, "alice", True),
            (BOB, "bob", False),
        ]

    def test_a_member_appears_under_at_most_one_team_per_page(self, client: TestClient) -> None:
        """⚠️ The collection loses members, on purpose. This is the discriminant.

        Upstream keys the page's members by **user id**, so a user who belongs to several
        of the page's teams collapses to one entry and lands in exactly one of them. An
        implementation that simply lists every team's members passes every other
        assertion about this collection — the ids, the order, the pagination, the search
        — and fails only this one.

        alice belongs to both T-ADMIN and T-PLAIN, which is what makes the page able to
        show the collapse at all; bob belongs to one, so he is the control that proves
        the response is not simply empty.
        """
        body = client.get("/api/v1/teams").json()

        appearances = [m["id"] for t in body for m in (t["members"] or [])]
        assert len(appearances) == len(set(appearances)), (
            f"a member id appears under two teams in one response: {appearances}"
        )
        assert ALICE in appearances and BOB in appearances

    def test_the_collapsed_member_lands_on_the_last_of_their_teams(
        self, client: TestClient
    ) -> None:
        """Which team wins is the row upstream's join yields last — the greatest
        team_members id among the page's teams. alice's T-PLAIN row is created after her
        T-ADMIN row, so T-PLAIN keeps her and T-ADMIN reports only bob."""
        by_id = {t["id"]: t for t in client.get("/api/v1/teams").json()}

        assert [m["id"] for m in by_id[T_ADMIN]["members"]] == [BOB]
        assert [m["id"] for m in by_id[T_PLAIN]["members"]] == [ALICE]

    def test_the_item_route_keeps_every_member(self, client: TestClient) -> None:
        """The other half of the pair, and the reason the two shapes cannot share a
        serializer: the same team read on its own carries both members."""
        assert [m["id"] for m in client.get(f"/api/v1/teams/{T_ADMIN}").json()["members"]] == [
            ALICE,
            BOB,
        ]

    def test_created_by_is_null_when_the_creator_is_on_no_team_of_the_page(
        self, client: TestClient, session: Session
    ) -> None:
        """Falls out of the same map. dave creates a team, alice is added to it, dave
        leaves — so the page contains a team whose creator is in nobody's membership and
        ``created_by`` is null, exactly as the seed's team 8 behaves upstream.

        The item route still resolves it, which is the pair that stops a serializer from
        nulling ``created_by`` everywhere.
        """
        session.add(Team(id=913, name="T-DAVE", created_by_id=DAVE, created=EPOCH, updated=EPOCH))
        session.add(TeamMember(team_id=913, user_id=ALICE, admin=False, created=EPOCH))
        session.commit()

        listed = {t["id"]: t for t in client.get("/api/v1/teams").json()}
        assert listed[913]["created_by"] is None
        assert client.get("/api/v1/teams/913").json()["created_by"]["id"] == DAVE

    def test_no_member_email_is_disclosed(self, client: TestClient) -> None:
        """A team is readable by all of its members, so an address here reaches everyone
        the team touches. Upstream blanks it before embedding."""
        members = client.get(f"/api/v1/teams/{T_ADMIN}").json()["members"]

        assert all("email" not in m for m in members)


# --- creating ----------------------------------------------------------------


class TestCreatingATeam:
    def test_a_name_is_enough(self, client: TestClient) -> None:
        resp = client.put("/api/v1/teams", json={"name": "new"})

        assert resp.status_code == 201
        assert resp.json()["name"] == "new"

    def test_an_empty_name_and_an_absent_one_are_the_same_412(self, client: TestClient) -> None:
        """One upstream case, two ways to spell it: Go decodes a missing key to "" and
        validates afterwards. A required field here would answer 422 for one and 412 for
        the other."""
        absent = client.put("/api/v1/teams", json={})
        empty = client.put("/api/v1/teams", json={"name": ""})

        assert absent.status_code == empty.status_code == 412
        assert absent.json() == empty.json()
        assert absent.json()["invalid_fields"] == ["name: non zero value required"]

    def test_a_whitespace_name_is_accepted(self, client: TestClient) -> None:
        """Upstream's "required" is non-zero, not non-blank. Nothing may strip() first."""
        resp = client.put("/api/v1/teams", json={"name": "   "})

        assert resp.status_code == 201

    def test_the_creator_becomes_an_admin_member(
        self, client: TestClient, session: Session
    ) -> None:
        """Not a convenience — it is the only thing granting the creator access
        afterwards. Skipping it leaves a team its author cannot read, and the create
        response would not show it either (see the next test)."""
        team_id = client.put("/api/v1/teams", json={"name": "new"}).json()["id"]
        session.expire_all()

        row = member_row(session, team_id, ALICE)
        assert row is not None and row.admin is True

    def test_the_create_response_omits_the_members_it_just_created(
        self, client: TestClient
    ) -> None:
        """``members: null`` even though the creator was just made an admin member.

        The create response is a view of the struct upstream built, not of the row that
        now exists. Hydrating it is the natural implementation and diverges on every
        create.
        """
        resp = client.put("/api/v1/teams", json={"name": "new"})

        assert resp.json()["members"] is None

    def test_the_create_response_zeroes_the_creators_timestamps(self, client: TestClient) -> None:
        """Same cause as the test above, on a different field. ``created_by`` echoes the
        authenticated subject rather than the stored user, so its id and username are
        real and its timestamps are zero — while any later read of the same team carries
        the real ones."""
        body = client.put("/api/v1/teams", json={"name": "new"}).json()

        assert body["created_by"]["id"] == ALICE
        assert body["created_by"]["username"] == "alice"
        assert body["created_by"]["created"] == "0001-01-01T00:00:00Z"
        assert body["created_by"]["updated"] == "0001-01-01T00:00:00Z"

    def test_a_later_read_of_that_team_carries_real_timestamps(self, client: TestClient) -> None:
        """The other half of the pair. Without it, a serializer that zeroed
        ``created_by`` everywhere would satisfy the test above."""
        team_id = client.put("/api/v1/teams", json={"name": "new"}).json()["id"]

        body = client.get(f"/api/v1/teams/{team_id}").json()
        assert body["created_by"]["created"] != "0001-01-01T00:00:00Z"
        assert [m["id"] for m in body["members"]] == [ALICE]

    def test_a_whole_read_body_may_be_posted_back(self, client: TestClient) -> None:
        """Read-modify-write: real clients GET the object and send all of it back,
        read-only fields included. Every one of them must be ignored, not rejected."""
        team_id = client.put("/api/v1/teams", json={"name": "new"}).json()["id"]
        echo = client.get(f"/api/v1/teams/{team_id}").json()

        resp = client.put("/api/v1/teams", json=echo)

        assert resp.status_code == 201


# --- updating ----------------------------------------------------------------


class TestUpdatingATeam:
    def test_an_admin_member_may_rename_it(self, client: TestClient, session: Session) -> None:
        resp = client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "renamed"})

        assert resp.status_code == 200
        session.expire_all()
        assert stored_team(session, T_ADMIN).name == "renamed"

    def test_a_plain_member_may_not(self, app: FastAPI, session: Session) -> None:
        resp = as_user(app, ALICE).post(f"/api/v1/teams/{T_PLAIN}", json={"name": "x"})

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}
        session.expire_all()
        assert stored_team(session, T_PLAIN).name == "T-PLAIN", "the write must not happen"

    def test_someone_elses_team_is_403_on_write(self, client: TestClient) -> None:
        """Existing but not administered by the caller. Pairs with the next test: this
        one must NOT reveal that the team exists."""
        resp = client.post(f"/api/v1/teams/{T_OTHER}", json={"name": "x"})

        assert resp.status_code == 403

    def test_a_missing_team_is_404_on_write(self, client: TestClient) -> None:
        """404/6002 where the read path answers 403.

        This is reachable only because ``TeamPolicy.can_update`` deliberately passes the
        missing case through to the service — a branch that reads like a hole. Deleting
        it turns this into a 403 and nothing else in the suite notices, which is exactly
        why this test names the code.
        """
        resp = client.post("/api/v1/teams/99999", json={"name": "x"})

        assert resp.status_code == 404
        assert resp.json() == {"code": 6002, "message": "This team does not exist."}

    def test_the_name_is_required_on_update_too(self, client: TestClient) -> None:
        resp = client.post(f"/api/v1/teams/{T_ADMIN}", json={})

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["name: non zero value required"]

    def test_omitting_the_description_keeps_it(self, client: TestClient, session: Session) -> None:
        client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "x"})
        session.expire_all()

        assert stored_team(session, T_ADMIN).description == "keep me"

    def test_an_explicitly_empty_description_also_keeps_it(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ This is the discriminating cell, and it is the one a reasonable person
        omits.

        With only the test above, "description is exempt from the replace" and "the write
        is guarded by a zero-value test" are the same rule. They are not: upstream builds
        the update with xorm's default column set, which skips zero values, so an
        explicit "" is indistinguishable from an omitted key — while a non-empty value
        writes normally (next test). Deleting this case lets a plain full-replace
        implementation pass.
        """
        client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "x", "description": ""})
        session.expire_all()

        assert stored_team(session, T_ADMIN).description == "keep me"

    def test_a_non_empty_description_is_written(self, client: TestClient, session: Session) -> None:
        client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "x", "description": "changed"})
        session.expire_all()

        assert stored_team(session, T_ADMIN).description == "changed"

    def test_is_public_is_a_full_replace(self, client: TestClient, session: Session) -> None:
        """The counterweight to the description cells. ``is_public`` really does reset
        when omitted — upstream forces the column with UseBool — so the two fields on one
        request obey different rules, and a test suite that only exercised one of them
        would certify whichever rule it happened to pick."""
        client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "x", "is_public": True})
        session.expire_all()
        assert stored_team(session, T_ADMIN).is_public is True

        client.post(f"/api/v1/teams/{T_ADMIN}", json={"name": "x"})
        session.expire_all()
        assert stored_team(session, T_ADMIN).is_public is False


# --- deleting ----------------------------------------------------------------


class TestDeletingATeam:
    def test_an_admin_member_may_delete_it(self, client: TestClient, session: Session) -> None:
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}")

        assert resp.status_code == 200
        assert resp.json() == {"message": "Successfully deleted."}
        session.expire_all()
        assert session.get(Team, T_ADMIN) is None

    def test_a_plain_member_may_not(self, app: FastAPI, session: Session) -> None:
        resp = as_user(app, ALICE).delete(f"/api/v1/teams/{T_PLAIN}")

        assert resp.status_code == 403
        session.expire_all()
        assert session.get(Team, T_PLAIN) is not None

    def test_the_membership_rows_go_too(self, client: TestClient, session: Session) -> None:
        """Not tidiness. ``visible_teams_query`` joins through ``team_members``, so an
        orphaned row makes a deleted team keep appearing in its members' lists — and,
        once an id is reused, appearing as some other team."""
        client.delete(f"/api/v1/teams/{T_ADMIN}")
        session.expire_all()

        assert member_row(session, T_ADMIN, ALICE) is None
        assert member_row(session, T_ADMIN, BOB) is None

    def test_the_project_grants_go_too(self, client: TestClient, session: Session) -> None:
        """A team's grant is one of the two paths to a project. Leaving it behind leaves
        a grant held by nobody, which the permission CTE would still join through."""
        session.add(Project(id=920, title="P", identifier="", owner_id=CAROL, position=1))
        session.add(TeamProject(team_id=T_ADMIN, project_id=920, permission=1))
        session.commit()

        client.delete(f"/api/v1/teams/{T_ADMIN}")
        session.expire_all()

        assert (
            session.query(TeamProject).filter(TeamProject.team_id == T_ADMIN).one_or_none() is None
        )


# --- members -----------------------------------------------------------------


class TestAddingAMember:
    def test_an_admin_adds_by_username(self, client: TestClient, session: Session) -> None:
        resp = client.put(f"/api/v1/teams/{T_ADMIN}/members", json={"username": "dave"})

        assert resp.status_code == 201
        assert resp.json()["username"] == "dave"
        assert resp.json()["admin"] is False
        session.expire_all()
        assert member_row(session, T_ADMIN, DAVE) is not None

    def test_the_response_id_is_the_membership_row_not_the_user(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ Both are ints called ``id`` and both are plausible. The team's own
        ``members[].id`` is the user id; this one is the ``team_members`` primary key.

        The fixture makes them distinguishable on purpose: dave's user id is 903 and no
        membership row will ever be numbered that, so returning the wrong one is visible.
        """
        body = client.put(f"/api/v1/teams/{T_ADMIN}/members", json={"username": "dave"}).json()
        session.expire_all()

        row = member_row(session, T_ADMIN, DAVE)
        assert row is not None
        assert body["id"] == row.id
        assert body["id"] != DAVE

    def test_a_user_id_in_the_body_is_a_412_about_username(self, client: TestClient) -> None:
        """The key is ``username``. A body of ``{"user_id": "dave"}`` leaves it empty and
        answers a validation error *about a field the client never sent* — a real,
        upstream, entirely credible message describing the request rather than the
        server. It cost a round of measurement here."""
        resp = client.put(f"/api/v1/teams/{T_ADMIN}/members", json={"user_id": "dave"})

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["username: non zero value required"]

    def test_an_unknown_username_is_404_1005(self, client: TestClient) -> None:
        resp = client.put(f"/api/v1/teams/{T_ADMIN}/members", json={"username": "nobody"})

        assert resp.status_code == 404
        assert resp.json() == {"code": 1005, "message": "The user does not exist."}

    def test_a_duplicate_is_409_6005(self, client: TestClient) -> None:
        resp = client.put(f"/api/v1/teams/{T_ADMIN}/members", json={"username": "bob"})

        assert resp.status_code == 409
        assert resp.json() == {
            "code": 6005,
            "message": "This user is already a member of that team.",
        }

    def test_a_plain_member_may_not_add(self, app: FastAPI, session: Session) -> None:
        resp = as_user(app, ALICE).put(
            f"/api/v1/teams/{T_PLAIN}/members", json={"username": "dave"}
        )

        assert resp.status_code == 403
        session.expire_all()
        assert member_row(session, T_PLAIN, DAVE) is None

    def test_a_missing_team_is_403_here_not_404(self, client: TestClient) -> None:
        """⚠️ The opposite of ``POST /teams/{id}``, on the same resource.

        Nobody administers a team that does not exist, so the permission check refuses
        first and never reaches an existence check. Answering 404 here — which reads like
        better diagnostics — would hand any authenticated caller a team-id oracle, and
        this route has no second gate to fall through to the way the CRUD writes do.
        """
        resp = client.put("/api/v1/teams/99999/members", json={"username": "dave"})

        assert resp.status_code == 403

    def test_permission_is_checked_before_the_username(self, client: TestClient) -> None:
        """Both checks would fire; the order decides what the caller learns. Answering
        1005 first would tell a non-admin which usernames exist."""
        resp = client.put(f"/api/v1/teams/{T_OTHER}/members", json={"username": "nobody"})

        assert resp.status_code == 403


class TestRemovingAMember:
    def test_an_admin_removes_by_username(self, client: TestClient, session: Session) -> None:
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}/members/bob")

        assert resp.status_code == 200
        assert resp.json() == {"message": "Successfully deleted."}
        session.expire_all()
        assert member_row(session, T_ADMIN, BOB) is None

    def test_a_numeric_segment_is_read_as_a_username(self, client: TestClient) -> None:
        """⚠️ The single most likely thing to get wrong on this resource.

        ``{username}`` is a username. ``DELETE /teams/{id}/members/901`` asks for a user
        *named* "901" — bob's id, deliberately, so an implementation that parsed the
        segment as an id would delete bob and answer 200. Upstream answers 404/1005.
        """
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}/members/{BOB}")

        assert resp.status_code == 404
        assert resp.json()["code"] == 1005

    def test_that_numeric_segment_removed_nobody(
        self, client: TestClient, session: Session
    ) -> None:
        """The status alone does not prove it: a handler could delete the row and still
        report 1005 afterwards."""
        client.delete(f"/api/v1/teams/{T_ADMIN}/members/{BOB}")
        session.expire_all()

        assert member_row(session, T_ADMIN, BOB) is not None

    def test_removing_a_non_member_is_200(self, client: TestClient) -> None:
        """dave is a real user who is simply not in this team. There is no 404 for "not
        a member" — only for "no such user"."""
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}/members/dave")

        assert resp.status_code == 200

    def test_removing_twice_is_200_both_times(self, client: TestClient, session: Session) -> None:
        """T_ADMIN starts with three members here (see the extra one added below), so
        neither call trips the last-member guard — which is what makes this case about
        idempotence rather than about that guard."""
        session.add(TeamMember(team_id=T_ADMIN, user_id=CAROL, admin=False, created=EPOCH))
        session.commit()

        first = client.delete(f"/api/v1/teams/{T_ADMIN}/members/bob")
        second = client.delete(f"/api/v1/teams/{T_ADMIN}/members/bob")

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

    def test_an_unknown_username_is_404_1005(self, client: TestClient) -> None:
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}/members/nobody")

        assert resp.status_code == 404
        assert resp.json()["code"] == 1005

    def test_the_last_admin_may_remove_themselves(
        self, client: TestClient, session: Session
    ) -> None:
        """The guard counts **members**, not admins, so this succeeds: bob is still here.

        ⚠️ This test passed against an implementation with no guard at all, which is how
        the guard came to be reported as absent in the first place — T_ADMIN has two
        members, so removing the admin never reaches the count. The A/B against the
        reference server is what found it; ``test_the_last_member_cannot_be_removed``
        below is the case that separates the two rules.
        """
        resp = client.delete(f"/api/v1/teams/{T_ADMIN}/members/alice")

        assert resp.status_code == 200
        session.expire_all()
        assert member_row(session, T_ADMIN, ALICE) is None

    def test_and_then_cannot_read_the_team_any_more(self, client: TestClient) -> None:
        """The consequence, asserted rather than assumed: access lives entirely in the
        membership row, so the team is now unreachable to its creator."""
        client.delete(f"/api/v1/teams/{T_ADMIN}/members/alice")

        assert client.get(f"/api/v1/teams/{T_ADMIN}").status_code == 403

    def test_and_the_survivor_can_no_longer_administer_it(self, app: FastAPI) -> None:
        """Removing the only admin leaves a team nobody administers. That state is
        reachable upstream, so it is reachable here: bob reads it at permission 0 and
        cannot add anyone. Nothing repairs it."""
        as_user(app, ALICE).delete(f"/api/v1/teams/{T_ADMIN}/members/alice")

        bob = as_user(app, BOB)
        assert bob.get(f"/api/v1/teams/{T_ADMIN}").headers["x-max-permission"] == "0"
        assert (
            bob.put(f"/api/v1/teams/{T_ADMIN}/members", json={"username": "dave"}).status_code
            == 403
        )

    def test_the_last_member_cannot_be_removed(self, app: FastAPI, session: Session) -> None:
        """400/6006, and T_PLAIN is the fixture that can show it: one member, alice.

        This is also the self-removal path — alice naming alice — so it doubles as proof
        that the guard sits *after* the permission check rather than instead of it.
        """
        resp = as_user(app, ALICE).delete(f"/api/v1/teams/{T_PLAIN}/members/alice")

        assert resp.status_code == 400
        assert resp.json() == {
            "code": 6006,
            "message": "You cannot delete the last member of a team.",
        }
        session.expire_all()
        assert member_row(session, T_PLAIN, ALICE) is not None

    def test_the_guard_fires_for_a_non_member_too(self, client: TestClient) -> None:
        """⚠️ The count is taken before anyone asks whether the named user is a member.

        Naming dave — a real user who was never in this team — answers 6006 rather than
        the 200 a non-member removal gets on any larger team. An implementation that
        checked membership first would answer 200 here and pass every other case here.

        The team is created through the API rather than reused from the fixture, and that
        is load-bearing: T_PLAIN has one member but alice is not its **admin**, so naming
        anyone other than herself is refused at the permission gate and never reaches the
        count. Writing it against T_PLAIN produced a 403 and looked like the guard was
        missing. A freshly created team makes its creator an admin, which is the only
        shape where a one-member team and an admin caller coexist.
        """
        team_id = client.put("/api/v1/teams", json={"name": "solo"}).json()["id"]

        resp = client.delete(f"/api/v1/teams/{team_id}/members/dave")

        assert resp.status_code == 400
        assert resp.json()["code"] == 6006

    def test_but_an_unknown_username_still_wins_over_the_guard(self, client: TestClient) -> None:
        """1005, not 6006, on a one-member team whose caller *is* its admin.

        The username lookup happens inside upstream's ``CanDelete``, ahead of both the
        permission decision and the count. This case and the one above differ only in
        whether the named user exists, which is the only way to order those two gates.
        """
        team_id = client.put("/api/v1/teams", json={"name": "solo"}).json()["id"]

        resp = client.delete(f"/api/v1/teams/{team_id}/members/nobody")

        assert resp.status_code == 404
        assert resp.json()["code"] == 1005

    def test_a_member_may_remove_themselves_without_being_an_admin(
        self, app: FastAPI, session: Session
    ) -> None:
        """⚠️ This route is **self-or-admin**, unlike the other two, which are admin-only.

        ``CanDelete`` returns true when the named user is the caller — the "leave the
        team" path. bob is a plain member of T_ADMIN and may leave it. Requiring admin
        here refuses every user's attempt to leave a team they were added to, which was
        this implementation's first behaviour and is the direction that looks safe.
        """
        resp = as_user(app, BOB).delete(f"/api/v1/teams/{T_ADMIN}/members/bob")

        assert resp.status_code == 200
        session.expire_all()
        assert member_row(session, T_ADMIN, BOB) is None

    def test_but_may_not_remove_somebody_else(self, app: FastAPI, session: Session) -> None:
        """The other half. Without it, "self-or-admin" and "anyone may remove anyone"
        are the same rule — bob leaving is permitted under both."""
        resp = as_user(app, BOB).delete(f"/api/v1/teams/{T_ADMIN}/members/alice")

        assert resp.status_code == 403
        session.expire_all()
        assert member_row(session, T_ADMIN, ALICE) is not None

    def test_a_non_member_may_not_remove_anyone(self, app: FastAPI, session: Session) -> None:
        resp = as_user(app, DAVE).delete(f"/api/v1/teams/{T_ADMIN}/members/bob")

        assert resp.status_code == 403
        session.expire_all()
        assert member_row(session, T_ADMIN, BOB) is not None


class TestTogglingAdmin:
    def test_an_admin_promotes_a_member(self, client: TestClient, session: Session) -> None:
        resp = client.post(f"/api/v1/teams/{T_ADMIN}/members/bob/admin")

        assert resp.status_code == 200
        assert resp.json()["admin"] is True
        session.expire_all()
        row = member_row(session, T_ADMIN, BOB)
        assert row is not None and row.admin is True

    def test_it_toggles_rather_than_sets(self, client: TestClient, session: Session) -> None:
        """Calling it twice returns bob to a plain member. An implementation that set the
        flag to true would pass the test above and diverge here."""
        client.post(f"/api/v1/teams/{T_ADMIN}/members/bob/admin")
        resp = client.post(f"/api/v1/teams/{T_ADMIN}/members/bob/admin")

        assert resp.json()["admin"] is False
        session.expire_all()
        row = member_row(session, T_ADMIN, BOB)
        assert row is not None and row.admin is False

    def test_promoting_a_non_member_is_a_200_that_writes_nothing(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ Reverse assertion. This looks like a bug and is upstream's behaviour.

        Upstream flips the flag on a struct it never loaded and serialises that, so the
        client gets ``admin: true`` with a zero id and a zero timestamp while the database
        is untouched. It is a 200, not a 5xx, so the rule is to copy it (practice 17)
        rather than to correct it (practice 23). Do not "fix" this into a 404 — this test
        is the only thing that would notice.
        """
        resp = client.post(f"/api/v1/teams/{T_ADMIN}/members/dave/admin")

        assert resp.status_code == 200
        assert resp.json() == {
            "id": 0,
            "username": "dave",
            "admin": True,
            "created": "0001-01-01T00:00:00Z",
        }
        session.expire_all()
        assert member_row(session, T_ADMIN, DAVE) is None

    def test_an_unknown_username_is_404_1005(self, client: TestClient) -> None:
        """The line between this and the case above: dave is a real user who is not a
        member (200), "nobody" is not a user at all (404)."""
        resp = client.post(f"/api/v1/teams/{T_ADMIN}/members/nobody/admin")

        assert resp.status_code == 404
        assert resp.json()["code"] == 1005

    def test_a_numeric_segment_is_read_as_a_username(self, client: TestClient) -> None:
        resp = client.post(f"/api/v1/teams/{T_ADMIN}/members/{BOB}/admin")

        assert resp.status_code == 404
        assert resp.json()["code"] == 1005

    def test_a_plain_member_may_not_promote(self, app: FastAPI, session: Session) -> None:
        resp = as_user(app, ALICE).post(f"/api/v1/teams/{T_PLAIN}/members/alice/admin")

        assert resp.status_code == 403
        session.expire_all()
        row = member_row(session, T_PLAIN, ALICE)
        assert row is not None and not row.admin


# --- wiring ------------------------------------------------------------------


class TestTheTeamRoutesAreWired:
    """Mounting and registering are two separate actions and only the first is visible
    from a request. Skipping the second does not break routing — it makes every
    API-token call to these paths 403 while JWT calls keep working."""

    def test_all_eight_operations_are_in_the_openapi_document(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        registered = {
            (method.upper(), path)
            for path, methods in paths.items()
            for method in methods
            if "/teams" in path
        }
        assert registered >= {
            ("GET", "/api/v1/teams"),
            ("PUT", "/api/v1/teams"),
            ("GET", "/api/v1/teams/{id}"),
            ("POST", "/api/v1/teams/{id}"),
            ("DELETE", "/api/v1/teams/{id}"),
            ("PUT", "/api/v1/teams/{id}/members"),
            ("DELETE", "/api/v1/teams/{id}/members/{username}"),
            ("POST", "/api/v1/teams/{id}/members/{username}/admin"),
        }

    def test_the_permission_keys_match_the_reference_servers_own_registry(self) -> None:
        """Measured from the reference server's ``GET /routes``: the teams group holds
        create/delete/members_admin/read_all/read_one/update and teams_members holds
        create/delete. ``members_admin`` is the interesting one — three non-parameter
        segments make it a sub-key of its parent rather than a group of its own, and
        naming it wrong would 403 every API-token call to that route alone."""
        from calton.core.route_registry import registry

        create_app()
        assert set(registry.to_json()["teams"]) == {
            "create",
            "delete",
            "members_admin",
            "read_all",
            "read_one",
            "update",
        }
        assert set(registry.to_json()["teams_members"]) == {"create", "delete"}


# --- the seam with the project permission model -------------------------------


class TestATeamGrantReachesTheProjectEndpoints:
    """Team membership is the **second** path to a project, beside ``users_projects``.

    The recursive permission CTE has had its team branch since T11 and
    ``test_project_permissions.py`` exercises it in 71 cases — but every one of those is
    service-level, and none of them goes through ``create_app``. So "the CTE knows about
    teams" was covered and "a team grant actually reaches an HTTP endpoint" was not: the
    seam belonged to neither file, which is the shape that has already cost this project
    five wired-but-not-connected modules.

    Measured against the reference server as a single state transition, and asserted here
    the same way — 403, grant, 200 at the granted level, promote, revoke, 403 again.
    A single "after the grant it is 200" assertion cannot tell a working grant from a
    project that was reachable all along, because the initial state is the one that has
    to be observed first.
    """

    @pytest.fixture
    def granted(self, session: Session) -> int:
        """A project carol owns, that dave can only reach through T_GRANT."""
        session.add(Project(id=930, title="carols", identifier="", owner_id=CAROL, position=1))
        session.add(Team(id=914, name="T-GRANT", created_by_id=CAROL, created=EPOCH, updated=EPOCH))
        session.add(TeamMember(team_id=914, user_id=DAVE, admin=False, created=EPOCH))
        session.commit()
        return 930

    def test_before_the_grant_it_is_invisible(self, app: FastAPI, granted: int) -> None:
        """The premise. Without it the whole sequence below is satisfied by a project
        dave could always see."""
        dave = as_user(app, DAVE)

        assert dave.get(f"/api/v1/projects/{granted}").status_code == 403
        assert granted not in [p["id"] for p in dave.get("/api/v1/projects").json()]

    def test_the_grant_makes_it_readable_at_the_granted_level(
        self, app: FastAPI, session: Session, granted: int
    ) -> None:
        session.add(TeamProject(team_id=914, project_id=granted, permission=1))
        session.commit()

        resp = as_user(app, DAVE).get(f"/api/v1/projects/{granted}")

        assert resp.status_code == 200
        assert resp.headers["x-max-permission"] == "1"

    def test_the_grant_also_puts_it_in_the_collection(
        self, app: FastAPI, session: Session, granted: int
    ) -> None:
        """A separate query from the one above — ``visible_projects_query`` rather than
        the CTE — with its own team branch. Reading one project and listing projects can
        disagree, so both are asserted."""
        session.add(TeamProject(team_id=914, project_id=granted, permission=1))
        session.commit()

        listed = [p["id"] for p in as_user(app, DAVE).get("/api/v1/projects").json()]

        assert granted in listed

    def test_the_grants_level_is_what_the_header_reports(
        self, app: FastAPI, session: Session, granted: int
    ) -> None:
        """Admin rather than write, so the header is not merely "some non-zero number"."""
        session.add(TeamProject(team_id=914, project_id=granted, permission=2))
        session.commit()

        resp = as_user(app, DAVE).get(f"/api/v1/projects/{granted}")

        assert resp.headers["x-max-permission"] == "2"

    def test_leaving_the_team_takes_the_access_away(
        self, app: FastAPI, session: Session, granted: int
    ) -> None:
        """The access lives in the membership row, not in the grant. Removing dave from
        the team must close the project again — otherwise the grant is a one-way door."""
        session.add(TeamProject(team_id=914, project_id=granted, permission=1))
        session.add(TeamMember(team_id=914, user_id=CAROL, admin=True, created=EPOCH))
        session.commit()
        assert as_user(app, DAVE).get(f"/api/v1/projects/{granted}").status_code == 200

        as_user(app, CAROL).delete("/api/v1/teams/914/members/dave")

        assert as_user(app, DAVE).get(f"/api/v1/projects/{granted}").status_code == 403

    def test_deleting_the_team_takes_the_access_away(
        self, app: FastAPI, session: Session, granted: int
    ) -> None:
        """The other way to lose it.

        ⚠️ **This case does not protect the ``team_projects`` cleanup**, and the docstring
        here used to claim it did. Mutation verification removed that cleanup and this
        test stayed green: deleting the team also deletes its ``team_members`` rows, so
        the CTE's INNER JOIN on membership fails and the answer is 403 whether or not the
        grant row survived. Two paths reach the same end state and only one of them is
        the mechanism named — practice 20's fixed point.

        What actually guards the grant cleanup is
        ``TestDeletingATeam::test_the_project_grants_go_too``, which asserts on the row
        rather than on the response; that one *did* go red under the same mutation. This
        case covers the user-visible half — access ends — and nothing more.
        """
        session.add(TeamProject(team_id=914, project_id=granted, permission=1))
        session.add(TeamMember(team_id=914, user_id=CAROL, admin=True, created=EPOCH))
        session.commit()
        assert as_user(app, DAVE).get(f"/api/v1/projects/{granted}").status_code == 200

        as_user(app, CAROL).delete("/api/v1/teams/914")

        assert as_user(app, DAVE).get(f"/api/v1/projects/{granted}").status_code == 403
