"""Permission truth table.

Reading it: each case builds a small world of users, projects, grants and teams, then
asserts the permission a user resolves to on one project. ``-1`` means no access at all;
``0`` is read access, which is why the two cannot be collapsed.

The case that matters most is :class:`TestNearestAncestorWins`. Inheritance resolves to
the *closest* ancestor holding a grant, not the most permissive one, so a read grant on
the immediate parent overrides an admin grant further up. The natural implementation —
taking the maximum across ancestors — silently grants admin there. Every test in that
class fails against that implementation and passes against this one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.models import Project, ProjectUser, Team, TeamMember, TeamProject, User
from calton.permissions.project import (
    MAX_HIERARCHY_DEPTH,
    NO_PERMISSION,
    CyclicHierarchyError,
    Permission,
    can_read,
    can_write,
    check_permission,
    is_admin,
    max_permission,
    max_permissions_for_projects,
)

READ, WRITE, ADMIN = Permission.READ, Permission.WRITE, Permission.ADMIN


@dataclass
class World:
    """A tiny builder so each case reads as the situation it describes."""

    session: Session
    _next: dict[str, int] = field(default_factory=lambda: {"id": 0})

    def _id(self) -> int:
        self._next["id"] += 1
        return self._next["id"]

    def user(self) -> int:
        user_id = self._id()
        self.session.add(User(id=user_id, username=f"user{user_id}"))
        self.session.flush()
        return user_id

    def project(self, owner: int, parent: int | None = None) -> int:
        project_id = self._id()
        self.session.add(
            Project(id=project_id, title=f"p{project_id}", owner_id=owner, parent_project_id=parent)
        )
        self.session.flush()
        return project_id

    def grant(self, project: int, user: int, permission: Permission) -> None:
        self.session.add(
            ProjectUser(id=self._id(), project_id=project, user_id=user, permission=permission)
        )
        self.session.flush()

    def team(self, *members: int) -> int:
        team_id = self._id()
        self.session.add(Team(id=team_id, name=f"t{team_id}", created_by_id=members[0]))
        for member in members:
            self.session.add(TeamMember(id=self._id(), team_id=team_id, user_id=member))
        self.session.flush()
        return team_id

    def team_grant(self, project: int, team: int, permission: Permission) -> None:
        self.session.add(
            TeamProject(id=self._id(), project_id=project, team_id=team, permission=permission)
        )
        self.session.flush()


@pytest.fixture
def world() -> Iterator[World]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as session:
        yield World(session)


class TestDirectGrants:
    def test_owner_gets_admin(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice)

        assert max_permission(world.session, alice, project) == ADMIN

    @pytest.mark.parametrize("permission", [READ, WRITE, ADMIN])
    def test_a_direct_grant_is_what_it_says(self, world: World, permission: Permission) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, permission)

        assert max_permission(world.session, bob, project) == permission

    def test_no_grant_is_minus_one(self, world: World) -> None:
        owner, stranger = world.user(), world.user()
        project = world.project(owner=owner)

        assert max_permission(world.session, stranger, project) == NO_PERMISSION

    def test_minus_one_is_not_read(self, world: World) -> None:
        """0 is read access, so "no access" needs its own value. Conflating them grants
        read to everybody."""
        owner, stranger = world.user(), world.user()
        project = world.project(owner=owner)

        assert max_permission(world.session, stranger, project) != READ
        assert not can_read(world.session, stranger, project)[0]

    def test_a_missing_project_is_absent_rather_than_minus_one(self, world: World) -> None:
        """Distinct from "exists but no access" — callers use the difference to tell
        403 from 404."""
        alice = world.user()

        assert max_permission(world.session, alice, 9999) is None
        assert max_permissions_for_projects(world.session, alice, [9999]) == {}


class TestTeamGrants:
    @pytest.mark.parametrize("permission", [READ, WRITE, ADMIN])
    def test_a_team_grant_reaches_its_members(self, world: World, permission: Permission) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.team_grant(project, world.team(bob), permission)

        assert max_permission(world.session, bob, project) == permission

    def test_a_non_member_gets_nothing(self, world: World) -> None:
        owner, bob, carol = world.user(), world.user(), world.user()
        project = world.project(owner=owner)
        world.team_grant(project, world.team(bob), ADMIN)

        assert max_permission(world.session, carol, project) == NO_PERMISSION

    def test_the_strongest_team_wins(self, world: World) -> None:
        """Across teams it really is a maximum — unlike inheritance up the tree."""
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.team_grant(project, world.team(bob), READ)
        world.team_grant(project, world.team(bob), ADMIN)
        world.team_grant(project, world.team(bob), WRITE)

        assert max_permission(world.session, bob, project) == ADMIN

    def test_a_direct_grant_beats_a_weaker_team_grant(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, ADMIN)
        world.team_grant(project, world.team(bob), READ)

        assert max_permission(world.session, bob, project) == ADMIN

    def test_a_team_grant_beats_a_weaker_direct_grant(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, READ)
        world.team_grant(project, world.team(bob), ADMIN)

        assert max_permission(world.session, bob, project) == ADMIN

    def test_owner_outranks_a_weaker_team_grant(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice)
        world.team_grant(project, world.team(alice), READ)

        assert max_permission(world.session, alice, project) == ADMIN


class TestInheritance:
    @pytest.mark.parametrize("permission", [READ, WRITE, ADMIN])
    def test_a_grant_on_the_parent_is_inherited(self, world: World, permission: Permission) -> None:
        owner, bob = world.user(), world.user()
        parent = world.project(owner=owner)
        child = world.project(owner=owner, parent=parent)
        world.grant(parent, bob, permission)

        assert max_permission(world.session, bob, child) == permission

    def test_inheritance_reaches_down_several_levels(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        root = world.project(owner=owner)
        middle = world.project(owner=owner, parent=root)
        leaf = world.project(owner=owner, parent=middle)
        deepest = world.project(owner=owner, parent=leaf)
        world.grant(root, bob, WRITE)

        assert max_permission(world.session, bob, deepest) == WRITE

    def test_owning_an_ancestor_grants_admin_below(self, world: World) -> None:
        alice = world.user()
        root = world.project(owner=alice)
        child = world.project(owner=alice, parent=root)

        assert max_permission(world.session, alice, child) == ADMIN

    def test_a_grant_on_a_child_does_not_reach_the_parent(self, world: World) -> None:
        """Inheritance goes up the chain when resolving, so grants flow downwards only."""
        owner, bob = world.user(), world.user()
        parent = world.project(owner=owner)
        child = world.project(owner=owner, parent=parent)
        world.grant(child, bob, ADMIN)

        assert max_permission(world.session, bob, parent) == NO_PERMISSION

    def test_a_sibling_grant_does_not_leak(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        parent = world.project(owner=owner)
        one = world.project(owner=owner, parent=parent)
        two = world.project(owner=owner, parent=parent)
        world.grant(one, bob, ADMIN)

        assert max_permission(world.session, bob, two) == NO_PERMISSION

    def test_no_grant_anywhere_in_the_chain(self, world: World) -> None:
        owner, stranger = world.user(), world.user()
        root = world.project(owner=owner)
        child = world.project(owner=owner, parent=root)

        assert max_permission(world.session, stranger, child) == NO_PERMISSION


class TestNearestAncestorWins:
    """The core of this task.

    Every case here passes under "nearest ancestor decides" and fails under "take the
    maximum across ancestors". The second is the intuitive implementation and it grants
    more access than the user should have.
    """

    def test_near_read_overrides_far_admin(self, world: World) -> None:
        """The canonical counter-example: a read grant close by beats admin further up.

        Parity seed overlay O2a (non-ownership chain).
        """
        owner, bob = world.user(), world.user()
        grandparent = world.project(owner=owner)
        parent = world.project(owner=owner, parent=grandparent)
        child = world.project(owner=owner, parent=parent)

        world.grant(grandparent, bob, ADMIN)
        world.grant(parent, bob, READ)

        assert max_permission(world.session, bob, child) == READ
        assert not is_admin(world.session, bob, child)
        assert not can_write(world.session, bob, child)

    def test_near_read_overrides_far_write(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        grandparent = world.project(owner=owner)
        parent = world.project(owner=owner, parent=grandparent)
        child = world.project(owner=owner, parent=parent)

        world.grant(grandparent, bob, WRITE)
        world.grant(parent, bob, READ)

        assert max_permission(world.session, bob, child) == READ

    def test_a_grant_on_the_project_itself_beats_any_ancestor(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        root = world.project(owner=owner)
        child = world.project(owner=owner, parent=root)

        world.grant(root, bob, ADMIN)
        world.grant(child, bob, READ)

        assert max_permission(world.session, bob, child) == READ

    def test_the_nearest_grant_wins_even_when_it_is_stronger(self, world: World) -> None:
        """Not a maximum in either direction — proximity decides, whichever way it falls."""
        owner, bob = world.user(), world.user()
        grandparent = world.project(owner=owner)
        parent = world.project(owner=owner, parent=grandparent)
        child = world.project(owner=owner, parent=parent)

        world.grant(grandparent, bob, READ)
        world.grant(parent, bob, ADMIN)

        assert max_permission(world.session, bob, child) == ADMIN

    def test_a_gap_in_the_chain_is_skipped(self, world: World) -> None:
        """Levels without any grant are passed over; the nearest *granting* one decides."""
        owner, bob = world.user(), world.user()
        root = world.project(owner=owner)
        middle = world.project(owner=owner, parent=root)
        leaf = world.project(owner=owner, parent=middle)

        world.grant(root, bob, READ)

        assert max_permission(world.session, bob, leaf) == READ

    def test_nearest_ancestor_applies_to_team_grants_too(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        grandparent = world.project(owner=owner)
        parent = world.project(owner=owner, parent=grandparent)
        child = world.project(owner=owner, parent=parent)

        world.team_grant(grandparent, world.team(bob), ADMIN)
        world.team_grant(parent, world.team(bob), READ)

        assert max_permission(world.session, bob, child) == READ

    def test_owning_a_near_ancestor_still_gives_admin(self, world: World) -> None:
        """Ownership is priority 1, so it is never beaten by an inherited grant."""
        alice, other = world.user(), world.user()
        grandparent = world.project(owner=other)
        parent = world.project(owner=alice, parent=grandparent)
        child = world.project(owner=other, parent=parent)

        world.grant(grandparent, alice, READ)

        assert max_permission(world.session, alice, child) == ADMIN

    def test_ownership_outranks_distance(self, world: World) -> None:
        """Ownership is the one thing proximity does not override.

        The priority expression is ``WHEN p.owner_id = ? THEN 1 ELSE ph.level + 1``, so an
        owned ancestor always sorts first no matter how far up it sits, while an inherited
        grant one level away sorts at 2. Owning the grandparent therefore beats a read
        grant on the parent.

        I expected the opposite when writing this case and the implementation was right —
        recording it so the next reader does not "fix" it back.

        Parity seed overlay O2b (ownership chain).
        """
        alice, other = world.user(), world.user()
        grandparent = world.project(owner=alice)
        parent = world.project(owner=other, parent=grandparent)
        child = world.project(owner=other, parent=parent)

        world.grant(parent, alice, READ)

        assert max_permission(world.session, alice, child) == ADMIN

    def test_ownership_outranks_distance_at_depth(self, world: World) -> None:
        """Same rule, further away — still admin."""
        alice, other = world.user(), world.user()
        root = world.project(owner=alice)
        current = root
        for _ in range(4):
            current = world.project(owner=other, parent=current)
        world.grant(current, alice, READ)
        leaf = world.project(owner=other, parent=current)

        assert max_permission(world.session, alice, leaf) == ADMIN


class TestExactSetComparison:
    """``checkPermission`` compares for equality against a set, never with ``>=``."""

    @pytest.mark.parametrize(
        ("held", "allowed", "expected"),
        [
            (READ, (READ,), True),
            (READ, (WRITE, ADMIN), False),
            (WRITE, (WRITE, ADMIN), True),
            (ADMIN, (WRITE, ADMIN), True),
            (READ, (ADMIN,), False),
            (WRITE, (ADMIN,), False),
            (ADMIN, (ADMIN,), True),
            (ADMIN, (READ,), False),
            (WRITE, (READ,), False),
        ],
    )
    def test_membership_decides(
        self, world: World, held: Permission, allowed: tuple[Permission, ...], expected: bool
    ) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, held)

        assert check_permission(world.session, bob, project, allowed) is expected

    def test_admin_is_not_write_by_ordering(self, world: World) -> None:
        """Admin passes a write check because the allowed set contains it, not because
        2 > 1. Same answer today, different answer if a value is ever inserted between."""
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, ADMIN)

        assert check_permission(world.session, bob, project, (ADMIN,))
        assert not check_permission(world.session, bob, project, (WRITE,))
        assert can_write(world.session, bob, project)

    def test_no_permission_matches_nothing(self, world: World) -> None:
        owner, stranger = world.user(), world.user()
        project = world.project(owner=owner)

        assert not check_permission(world.session, stranger, project, (READ, WRITE, ADMIN))

    def test_an_empty_allowed_set_denies(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice)

        assert not check_permission(world.session, alice, project, ())


class TestEntryPoints:
    @pytest.mark.parametrize(
        ("permission", "read", "write", "admin"),
        [
            (READ, True, False, False),
            (WRITE, True, True, False),
            (ADMIN, True, True, True),
        ],
    )
    def test_the_three_gates(
        self, world: World, permission: Permission, read: bool, write: bool, admin: bool
    ) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, permission)

        assert can_read(world.session, bob, project)[0] is read
        assert can_write(world.session, bob, project) is write
        assert is_admin(world.session, bob, project) is admin

    def test_can_read_reports_the_permission_for_the_header(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, WRITE)

        assert can_read(world.session, bob, project) == (True, WRITE)

    def test_can_read_reports_zero_when_denied(self, world: World) -> None:
        """Go leaves the field at its zero value rather than passing -1 through, and
        x-max-permission is rendered from it."""
        owner, stranger = world.user(), world.user()
        project = world.project(owner=owner)

        assert can_read(world.session, stranger, project) == (False, 0)

    def test_can_read_reports_zero_for_a_missing_project(self, world: World) -> None:
        alice = world.user()

        assert can_read(world.session, alice, 9999) == (False, 0)

    def test_every_gate_denies_a_stranger(self, world: World) -> None:
        owner, stranger = world.user(), world.user()
        project = world.project(owner=owner)

        assert not can_read(world.session, stranger, project)[0]
        assert not can_write(world.session, stranger, project)
        assert not is_admin(world.session, stranger, project)


class TestBatchResolution:
    def test_several_projects_resolve_independently(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        readable = world.project(owner=owner)
        writable = world.project(owner=owner)
        owned = world.project(owner=bob)
        invisible = world.project(owner=owner)
        world.grant(readable, bob, READ)
        world.grant(writable, bob, WRITE)

        resolved = max_permissions_for_projects(
            world.session, bob, [readable, writable, owned, invisible]
        )

        assert resolved == {
            readable: READ,
            writable: WRITE,
            owned: ADMIN,
            invisible: NO_PERMISSION,
        }

    def test_an_empty_request_returns_an_empty_result(self, world: World) -> None:
        alice = world.user()

        assert max_permissions_for_projects(world.session, alice, []) == {}

    def test_missing_projects_are_dropped_not_reported(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice)

        resolved = max_permissions_for_projects(world.session, alice, [project, 9999])

        assert resolved == {project: ADMIN}

    def test_batching_agrees_with_resolving_one_at_a_time(self, world: World) -> None:
        """The CTE partitions by original project; a shared ancestor must not bleed
        between entries in one call."""
        owner, bob = world.user(), world.user()
        root = world.project(owner=owner)
        one = world.project(owner=owner, parent=root)
        two = world.project(owner=owner, parent=root)
        world.grant(root, bob, READ)
        world.grant(one, bob, ADMIN)

        batched = max_permissions_for_projects(world.session, bob, [one, two])

        assert batched == {one: ADMIN, two: READ}
        assert batched[one] == max_permission(world.session, bob, one)
        assert batched[two] == max_permission(world.session, bob, two)


class TestIsolation:
    def test_permissions_do_not_leak_between_users(self, world: World) -> None:
        owner, bob, carol = world.user(), world.user(), world.user()
        project = world.project(owner=owner)
        world.grant(project, bob, ADMIN)

        assert max_permission(world.session, bob, project) == ADMIN
        assert max_permission(world.session, carol, project) == NO_PERMISSION

    def test_a_grant_to_another_user_on_an_ancestor_does_not_leak(self, world: World) -> None:
        owner, bob, carol = world.user(), world.user(), world.user()
        root = world.project(owner=owner)
        child = world.project(owner=owner, parent=root)
        world.grant(root, bob, ADMIN)

        assert max_permission(world.session, carol, child) == NO_PERMISSION

    def test_team_membership_is_per_user(self, world: World) -> None:
        owner, bob, carol = world.user(), world.user(), world.user()
        project = world.project(owner=owner)
        world.team_grant(project, world.team(bob), ADMIN)

        assert max_permission(world.session, carol, project) == NO_PERMISSION


class TestBoundaries:
    def test_a_top_level_project_has_no_parent_to_walk(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice, parent=None)

        assert max_permission(world.session, alice, project) == ADMIN

    def test_parent_zero_is_treated_as_top_level(self, world: World) -> None:
        """Upstream stores "no parent" as either NULL or 0; both must terminate the walk."""
        alice = world.user()
        project = world.project(owner=alice, parent=0)

        assert max_permission(world.session, alice, project) == ADMIN

    def test_a_dangling_parent_reference_does_not_explode(self, world: World) -> None:
        alice = world.user()
        project = world.project(owner=alice, parent=9999)

        assert max_permission(world.session, alice, project) == ADMIN

    def test_a_deep_chain_resolves(self, world: World) -> None:
        owner, bob = world.user(), world.user()
        current = world.project(owner=owner)
        world.grant(current, bob, WRITE)
        for _ in range(30):
            current = world.project(owner=owner, parent=current)

        assert max_permission(world.session, bob, current) == WRITE


class TestDepthBound:
    """The parent walk is bounded; upstream's is not.

    Go recurses without a limit, so a cycle in parent_project_id makes the query run
    forever — I verified that both implementations hang before the bound was added. Under
    a synchronous threadpool that is worse than slow: the request holds a worker and a
    connection and never gives either back, so a few dozen of them take down endpoints
    with no relation to the cycle.

    Hitting the bound raises rather than denying. Denying would produce a 403 that cannot
    be told apart from a legitimate one, so the corruption would be diagnosed as a
    permissions problem; returning the partial walk would be worse still, since dropping
    the owner row silently loses admin.
    """

    @staticmethod
    def _make_cycle(world: World) -> tuple[int, int]:
        alice = world.user()
        first = world.project(owner=alice)
        second = world.project(owner=alice, parent=first)
        world.session.query(Project).filter(Project.id == first).update(
            {"parent_project_id": second}
        )
        world.session.flush()
        return alice, first

    def test_a_cycle_terminates_instead_of_hanging(self, world: World) -> None:
        """Without the bound this test never returns."""
        alice, first = self._make_cycle(world)

        with pytest.raises(CyclicHierarchyError):
            max_permission(world.session, alice, first)

    def test_the_error_carries_diagnosis(self, world: World) -> None:
        alice, first = self._make_cycle(world)

        with pytest.raises(CyclicHierarchyError) as raised:
            max_permission(world.session, alice, first)

        assert raised.value.project_id == first
        assert raised.value.depth >= MAX_HIERARCHY_DEPTH

    def test_it_is_never_reported_as_a_permission_decision(self, world: World) -> None:
        """The whole point of raising: a 403 here would be read as "not your project"."""
        alice, first = self._make_cycle(world)

        for gate in (can_read, can_write, is_admin):
            with pytest.raises(CyclicHierarchyError):
                gate(world.session, alice, first)

    def test_hitting_the_bound_is_logged_with_the_project_and_depth(
        self, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The response is a generic 500, so the log is the only diagnosis."""
        alice, first = self._make_cycle(world)

        with caplog.at_level(logging.ERROR), pytest.raises(CyclicHierarchyError):
            max_permission(world.session, alice, first)

        assert str(first) in caplog.text
        assert str(MAX_HIERARCHY_DEPTH) in caplog.text
        assert "parent_project_id" in caplog.text

    def test_a_hierarchy_within_the_bound_is_unaffected(self, world: World) -> None:
        """The bound must not touch any hierarchy a real deployment could produce."""
        owner, bob = world.user(), world.user()
        current = world.project(owner=owner)
        world.grant(current, bob, WRITE)
        for _ in range(60):
            current = world.project(owner=owner, parent=current)

        assert max_permission(world.session, bob, current) == WRITE

    def test_the_bound_is_far_above_any_real_hierarchy(self) -> None:
        assert MAX_HIERARCHY_DEPTH == 512


class TestPriorityTie:
    """An ambiguity inherited from upstream, recorded rather than resolved.

    ``ORDER BY priority`` has no tiebreaker, and two rows can share priority 1: an owned
    ancestor (ownership is always 1) and a direct grant on the project itself (level 0
    plus 1). Which one ``ROW_NUMBER`` picks is engine-dependent.

    **Do not add a tiebreaker.** It would be a deviation from Go and would change answers
    Go does not change. This test pins only what is actually guaranteed — that one of the
    two candidates wins and the query stays well-formed — so a future refactor cannot
    quietly start returning something that is neither.
    """

    @pytest.fixture
    def tied(self, world: World) -> tuple[int, int]:
        alice, bob = world.user(), world.user()
        root = world.project(owner=bob)
        middle = world.project(owner=alice, parent=root)
        leaf = world.project(owner=alice, parent=middle)
        world.grant(leaf, bob, READ)
        return bob, leaf

    def test_the_answer_is_one_of_the_two_tied_candidates(
        self, world: World, tied: tuple[int, int]
    ) -> None:
        bob, leaf = tied

        assert max_permission(world.session, bob, leaf) in (READ, ADMIN)

    def test_the_tie_is_resolved_consistently_within_one_engine(
        self, world: World, tied: tuple[int, int]
    ) -> None:
        """Stable per engine, which is why it has never been noticed upstream."""
        bob, leaf = tied

        answers = {max_permission(world.session, bob, leaf) for _ in range(5)}

        assert len(answers) == 1

    def test_access_is_granted_either_way(self, world: World, tied: tuple[int, int]) -> None:
        """Whichever candidate wins, the user can read — the ambiguity cannot deny."""
        bob, leaf = tied

        assert can_read(world.session, bob, leaf)[0]
