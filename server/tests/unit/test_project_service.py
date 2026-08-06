"""Project create/update/delete semantics.

Two groups here block a merge on their own.

:class:`TestReparentGates` is the five-cell table behind CVE-2026-35595 and
CVE-2026-55064. The cell most often missing is the second: sending
``parent_project_id`` with the value it *already* has must stay an ordinary write, since
gating on "the field was present" rather than "the value changed" locks out every
collaborator doing a read-modify-write.

:class:`TestDelete` covers the cascade, including a child owned by somebody else. The
instinctive fix — re-checking permission inside the recursion — leaves an orphaned child
behind and diverges from upstream.

:class:`TestUpdateColumnWhitelist` covers the part that reads like a bug and is not: a
description cannot be cleared once set. The control in
``test_hex_color_is_reset_while_description_survives`` is what makes that assertion
meaningful, because it shows the same request resetting a field that *is*
unconditionally written. Without the pair, "description survived" is indistinguishable
from "this endpoint does partial updates".
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.models import ProjectUser, TeamProject
from calton.models.bucket import Bucket
from calton.models.project import Project
from calton.models.project_view import ProjectView
from calton.models.saved_filter import Favorite
from calton.models.task import Task
from calton.models.user import User
from calton.permissions.project import Permission
from calton.services.project_service import (
    DEFAULT_VIEWS,
    FAVORITE_KIND_PROJECT,
    UNCONDITIONAL_UPDATE_COLUMNS,
    archived_project_ids,
    create_project,
    delete_project,
    reads_as_archived,
    update_project,
)

OWNER = 1
COLLABORATOR = 2
STRANGER = 3


@pytest.fixture
def session() -> Iterator[Session]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as db:
        for user_id, name in ((OWNER, "owner"), (COLLABORATOR, "collab"), (STRANGER, "stranger")):
            db.add(User(id=user_id, username=name))
        db.flush()
        yield db


def grant(session: Session, project_id: int, user_id: int, permission: Permission) -> None:
    session.add(ProjectUser(project_id=project_id, user_id=user_id, permission=permission))
    session.flush()


def make_project(
    session: Session,
    *,
    owner: int = OWNER,
    parent: int | None = None,
    title: str = "p",
    description: str | None = None,
    hex_color: str | None = None,
) -> Project:
    return create_project(
        session,
        owner_id=owner,
        title=title,
        parent_project_id=parent,
        description=description,
        hex_color=hex_color,
    )


def nest_under(session: Session, child: Project, parent: Project) -> Project:
    """Put ``child`` under ``parent`` by writing the column, bypassing the create gate.

    ⚠️ Deliberately not ``make_project(parent=...)``. Creating under a project you hold
    nothing on is 403 (upstream too — see :class:`TestCreatingUnderSomeoneElsesProject`),
    so a fixture that builds "a child owned by somebody else" through the create path is
    asserting against a state that path cannot produce. The state itself is perfectly
    real — a grant can be revoked, or an admin can move a project — so the row is written
    directly and the test keeps testing what it is named for.
    """
    child.parent_project_id = parent.id
    session.flush()
    return child


class TestDefaultViews:
    def test_a_new_project_gets_exactly_four_views(self, session: Session) -> None:
        project = make_project(session)

        views = list(
            session.scalars(
                select(ProjectView)
                .where(ProjectView.project_id == project.id)
                .order_by(ProjectView.position)
            )
        )

        assert len(views) == 4

    def test_each_view_matches_upstream_field_for_field(self, session: Session) -> None:
        project = make_project(session)

        views = list(
            session.scalars(
                select(ProjectView)
                .where(ProjectView.project_id == project.id)
                .order_by(ProjectView.position)
            )
        )

        assert [(v.title, v.view_kind, v.position) for v in views] == [
            ("List", 0, 100),
            ("Gantt", 1, 200),
            ("Table", 2, 300),
            ("Kanban", 3, 400),
        ]

    def test_only_the_list_view_carries_a_filter(self, session: Session) -> None:
        project = make_project(session)
        views = {
            v.title: v
            for v in session.scalars(
                select(ProjectView).where(ProjectView.project_id == project.id)
            )
        }

        # ⚠️ The column holds the whole marshalled TaskCollection, not the expression —
        # measured on a project created by the Go reference server. This assertion used to
        # expect the bare string, which made the default views' spelling of a filter differ
        # from the one PUT /projects/{p}/views writes, so the same view read back
        # differently depending on which path created it (T17).
        list_filter = views["List"].filter
        assert list_filter is not None
        assert json.loads(list_filter) == {
            "s": "",
            "sort_by": None,
            "order_by": None,
            "filter": "done = false",
            "filter_include_nulls": False,
        }
        assert [views[name].filter for name in ("Gantt", "Table", "Kanban")] == [None] * 3

    def test_only_kanban_is_manually_bucketed(self, session: Session) -> None:
        project = make_project(session)
        views = {
            v.title: v
            for v in session.scalars(
                select(ProjectView).where(ProjectView.project_id == project.id)
            )
        }

        assert views["Kanban"].bucket_configuration_mode == 1
        assert [views[n].bucket_configuration_mode for n in ("List", "Gantt", "Table")] == [0] * 3

    def test_the_specification_is_four_views(self) -> None:
        """Guards the constant itself, which the tests above read through."""
        assert len(DEFAULT_VIEWS) == 4


class TestUpdateColumnWhitelist:
    """``project.go:1256-1270``. Two of the columns are conditional."""

    def _update(
        self,
        session: Session,
        project: Project,
        *,
        title: str = "p",
        description: str | None = None,
        identifier: str = "",
    ) -> Project:
        return update_project(
            session,
            project=project,
            user_id=OWNER,
            title=title,
            description=description,
            identifier=identifier,
        )

    def test_a_description_cannot_be_cleared_by_omitting_it(self, session: Session) -> None:
        project = make_project(session, description="x")

        self._update(session, project)

        assert project.description == "x"

    def test_a_description_cannot_be_cleared_by_sending_an_empty_string(
        self, session: Session
    ) -> None:
        """The empty string *is* the "do not update" signal, so there is no way to clear."""
        project = make_project(session, description="x")

        self._update(session, project, description="")

        assert project.description == "x"

    def test_a_description_cannot_be_cleared_by_sending_null(self, session: Session) -> None:
        project = make_project(session, description="x")

        self._update(session, project, description=None)

        assert project.description == "x"

    def test_a_non_empty_description_does_replace_the_old_one(self, session: Session) -> None:
        project = make_project(session, description="x")

        self._update(session, project, description="y")

        assert project.description == "y"

    def test_hex_color_is_reset_while_description_survives(self, session: Session) -> None:
        """The control that gives the description assertions meaning.

        Both fields are omitted from the same request. ``hex_color`` is written
        unconditionally so it resets; ``description`` is conditional so it does not. If
        this endpoint were doing partial updates, both would survive.
        """
        project = make_project(session, description="x", hex_color="ff0000")

        self._update(session, project)

        # "" rather than None: hex_color is a plain Go string, so its zero value is the
        # empty string. The recorded contract shows exactly that on a fresh project.
        assert project.hex_color == ""
        assert project.description == "x"

    def test_owner_created_and_updated_are_not_in_the_whitelist(self, session: Session) -> None:
        """Which is what makes echoing them back harmless — no schema rule is doing it."""
        project = make_project(session)
        original_owner, original_created = project.owner_id, project.created

        self._update(session, project, title="renamed")

        assert project.owner_id == original_owner
        assert project.created == original_created

    def test_identifier_is_stored_uppercase(self, session: Session) -> None:
        project = make_project(session)

        self._update(session, project, identifier="abc")

        assert project.identifier == "ABC"


class TestParentProjectIdFourCells:
    """Omitted, null, explicit zero, explicit value — all four are needed."""

    def test_omitting_the_parent_keeps_it(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)

        update_project(session, project=child, user_id=OWNER, title="child")

        assert child.parent_project_id == parent.id

    def test_an_explicit_null_is_the_same_as_omitting(self, session: Session) -> None:
        """A third-party client or MCP will send this; our own UI never does.

        Reading it as "detach" — which is what keying off Pydantic's ``model_fields_set``
        would do — flattens the project tree.
        """
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)

        update_project(session, project=child, user_id=OWNER, title="child", parent_project_id=None)

        assert child.parent_project_id == parent.id

    def test_an_explicit_zero_detaches_to_the_top_level(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)

        update_project(session, project=child, user_id=OWNER, title="child", parent_project_id=0)

        assert child.parent_project_id == 0

    def test_an_explicit_value_moves_the_project(self, session: Session) -> None:
        old_parent = make_project(session, title="old")
        new_parent = make_project(session, title="new")
        child = make_project(session, title="child", parent=old_parent.id)

        update_project(
            session,
            project=child,
            user_id=OWNER,
            title="child",
            parent_project_id=new_parent.id,
        )

        assert child.parent_project_id == new_parent.id


class TestReparentGates:
    """The five @critical cells. Any one of them red blocks the merge."""

    def test_write_user_editing_the_title_without_touching_the_parent(
        self, session: Session
    ) -> None:
        """Cell 1. Catches an implementation that made ordinary updates Admin-only."""
        project = make_project(session)
        grant(session, project.id, COLLABORATOR, Permission.WRITE)

        update_project(session, project=project, user_id=COLLABORATOR, title="renamed")

        assert project.title == "renamed"

    def test_write_user_sending_the_parent_unchanged(self, session: Session) -> None:
        """Cell 2, the one most often missing.

        The gate keys off the value *changing*, not the field being present. Gating on
        presence locks out every collaborator doing a read-modify-write, which is what
        MCP clients do on every update.
        """
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grant(session, child.id, COLLABORATOR, Permission.WRITE)

        update_project(
            session,
            project=child,
            user_id=COLLABORATOR,
            title="renamed",
            parent_project_id=parent.id,
        )

        assert child.title == "renamed"
        assert child.parent_project_id == parent.id

    def test_write_user_changing_the_parent_is_forbidden(self, session: Session) -> None:
        """Cell 3: write on the moved project is not enough."""
        old_parent = make_project(session, title="old")
        new_parent = make_project(session, title="new")
        child = make_project(session, title="child", parent=old_parent.id)
        grant(session, child.id, COLLABORATOR, Permission.WRITE)
        grant(session, new_parent.id, COLLABORATOR, Permission.ADMIN)

        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=child,
                user_id=COLLABORATOR,
                title="child",
                parent_project_id=new_parent.id,
            )

        assert excinfo.value.code == 1
        assert excinfo.value.http_status == 403

    def test_admin_attaching_under_a_parent_they_only_have_write_on(self, session: Session) -> None:
        """Cell 4, the vulnerability itself.

        Checking only ``CanWrite`` on the new parent lets a user attach a shared project
        under one they control and inherit Admin over it.
        """
        old_parent = make_project(session, title="old")
        new_parent = make_project(session, title="new")
        child = make_project(session, title="child", parent=old_parent.id)
        grant(session, child.id, COLLABORATOR, Permission.ADMIN)
        grant(session, new_parent.id, COLLABORATOR, Permission.WRITE)

        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=child,
                user_id=COLLABORATOR,
                title="child",
                parent_project_id=new_parent.id,
            )

        assert excinfo.value.code == 1

    def test_admin_detaching_to_the_top_level_is_allowed(self, session: Session) -> None:
        """Cell 5a: detaching needs Admin on the moved project only — there is no parent."""
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grant(session, child.id, COLLABORATOR, Permission.ADMIN)

        update_project(
            session,
            project=child,
            user_id=COLLABORATOR,
            title="child",
            parent_project_id=0,
        )

        assert child.parent_project_id == 0

    def test_non_admin_detaching_to_the_top_level_is_forbidden(self, session: Session) -> None:
        """Cell 5b (CVE-2026-55064): detaching severs an owner's inherited permissions."""
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grant(session, child.id, COLLABORATOR, Permission.WRITE)

        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=child,
                user_id=COLLABORATOR,
                title="child",
                parent_project_id=0,
            )

        assert excinfo.value.code == 1

    def test_admin_on_both_sides_may_attach(self, session: Session) -> None:
        """The positive control: with Admin on both, the move goes through."""
        old_parent = make_project(session, title="old")
        new_parent = make_project(session, title="new")
        child = make_project(session, title="child", parent=old_parent.id)
        grant(session, child.id, COLLABORATOR, Permission.ADMIN)
        grant(session, new_parent.id, COLLABORATOR, Permission.ADMIN)

        update_project(
            session,
            project=child,
            user_id=COLLABORATOR,
            title="child",
            parent_project_id=new_parent.id,
        )

        assert child.parent_project_id == new_parent.id


class TestCycleCheck:
    """Hard dependency (1). Without it the read-side depth cap is the only defence, and
    that reports corrupt data as "no permission"."""

    def test_a_project_cannot_be_its_own_parent(self, session: Session) -> None:
        project = make_project(session)

        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=project,
                user_id=OWNER,
                title="p",
                parent_project_id=project.id,
            )

        assert excinfo.value.code == 3010

    def test_a_two_step_cycle_is_refused(self, session: Session) -> None:
        grandparent = make_project(session, title="gp")
        parent = make_project(session, title="p", parent=grandparent.id)

        # Making the grandparent a child of its own descendant closes the loop.
        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=grandparent,
                user_id=OWNER,
                title="gp",
                parent_project_id=parent.id,
            )

        assert excinfo.value.code == 3011

    def test_a_legitimate_deep_hierarchy_is_still_allowed(self, session: Session) -> None:
        first = make_project(session, title="a")
        second = make_project(session, title="b", parent=first.id)
        third = make_project(session, title="c")

        update_project(
            session, project=third, user_id=OWNER, title="c", parent_project_id=second.id
        )

        assert third.parent_project_id == second.id


class TestDelete:
    """Hard dependency (5): fully recursive, hard, and unchecked on the way down."""

    def test_children_and_grandchildren_go_too(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grandchild = make_project(session, title="grandchild", parent=child.id)
        child_id, grandchild_id = child.id, grandchild.id

        delete_project(session, project=parent)

        assert session.get(Project, child_id) is None
        assert session.get(Project, grandchild_id) is None

    def test_tasks_are_physically_removed(self, session: Session) -> None:
        project = make_project(session)
        session.add(Task(project_id=project.id, title="t", index=1, created_by_id=OWNER))
        session.flush()

        delete_project(session, project=project)

        assert list(session.scalars(select(Task))) == []

    def test_already_soft_deleted_tasks_are_removed_as_well(self, session: Session) -> None:
        """There is nothing left to restore them into once the project is gone."""
        from datetime import UTC, datetime

        project = make_project(session)
        session.add(
            Task(
                project_id=project.id,
                title="soft",
                index=1,
                created_by_id=OWNER,
                deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.flush()

        delete_project(session, project=project)

        assert session.execute(select(Task)).all() == []

    def test_a_child_owned_by_somebody_else_is_deleted_too(self, session: Session) -> None:
        """The cascade does not re-check permission. Adding a check here would leave an
        orphan and diverge from upstream."""
        parent = make_project(session, title="parent", owner=OWNER)
        child = nest_under(session, make_project(session, title="child", owner=STRANGER), parent)
        child_id = child.id

        delete_project(session, project=parent)

        assert session.get(Project, child_id) is None

    def test_views_are_removed_with_the_project(self, session: Session) -> None:
        project = make_project(session)

        delete_project(session, project=project)

        assert list(session.scalars(select(ProjectView))) == []

    def test_an_unrelated_project_is_untouched(self, session: Session) -> None:
        doomed = make_project(session, title="doomed")
        keeper = make_project(session, title="keeper")
        keeper_id = keeper.id

        delete_project(session, project=doomed)

        assert session.get(Project, keeper_id) is not None
        assert len(list(session.scalars(select(ProjectView)))) == 4


class TestDefaultPosition:
    """``calculateDefaultPosition``: an unset position is derived from the id.

    Measured against the reference server, where consecutively created projects came
    back with 131072, 196608 and 262144 — id times 2^16. The gap is what lets a
    drag-and-drop reorder insert between two neighbours without renumbering.
    """

    def test_position_defaults_to_the_id_scaled(self, session: Session) -> None:
        project = make_project(session)

        assert project.position == project.id * 65536

    def test_an_explicit_position_is_kept(self, session: Session) -> None:
        project = create_project(session, owner_id=OWNER, title="p", position=12.5)

        assert project.position == 12.5

    def test_consecutive_projects_leave_a_gap_to_insert_into(self, session: Session) -> None:
        first = make_project(session, title="a")
        second = make_project(session, title="b")

        assert first.position is not None and second.position is not None
        assert second.position - first.position == 65536

    def test_default_views_keep_their_explicit_positions(self, session: Session) -> None:
        """They are created with 100/200/300/400, which are non-zero and so preserved."""
        project = make_project(session)

        positions = sorted(
            v.position or 0
            for v in session.scalars(
                select(ProjectView).where(ProjectView.project_id == project.id)
            )
        )

        assert positions == [100, 200, 300, 400]


class TestDefaultBuckets:
    """Creating a project also creates the Kanban view's three buckets.

    Card item ①-a. The recorded contract is the evidence this belongs to project
    creation rather than to bucket CRUD: the Kanban view comes back with
    ``default_bucket_id`` and ``done_bucket_id`` already populated and two apart, with
    Doing sitting between them.
    """

    def _kanban(self, session: Session, project: Project) -> ProjectView:
        view = session.scalars(
            select(ProjectView).where(
                ProjectView.project_id == project.id, ProjectView.title == "Kanban"
            )
        ).one()
        return view

    def test_three_buckets_are_created(self, session: Session) -> None:
        project = make_project(session)
        view = self._kanban(session, project)

        buckets = list(
            session.scalars(
                select(Bucket).where(Bucket.project_view_id == view.id).order_by(Bucket.position)
            )
        )

        assert [(b.title, b.position) for b in buckets] == [
            ("To-Do", 100),
            ("Doing", 200),
            ("Done", 300),
        ]

    def test_only_the_kanban_view_gets_buckets(self, session: Session) -> None:
        project = make_project(session)

        view_ids = list(
            session.scalars(select(ProjectView.id).where(ProjectView.project_id == project.id))
        )
        bucket_view_ids = set(
            session.scalars(
                select(Bucket.project_view_id).where(Bucket.project_view_id.in_(view_ids))
            )
        )

        assert bucket_view_ids == {self._kanban(session, project).id}

    def test_the_view_points_at_the_first_and_last_bucket(self, session: Session) -> None:
        """Two apart, because Doing sits between them — as the recorded contract shows."""
        project = make_project(session)
        view = self._kanban(session, project)

        buckets = list(
            session.scalars(
                select(Bucket).where(Bucket.project_view_id == view.id).order_by(Bucket.position)
            )
        )

        assert view.default_bucket_id == buckets[0].id
        assert view.done_bucket_id == buckets[-1].id
        assert view.done_bucket_id - view.default_bucket_id == 2

    def test_buckets_go_when_the_project_does(self, session: Session) -> None:
        project = make_project(session)

        delete_project(session, project=project)

        assert list(session.scalars(select(Bucket))) == []


class TestCreatingUnderSomeoneElsesProject:
    """Creating **under** a parent needs Write on it — and create ≠ update here.

    ⚠️ The whole point of this class is the Write/Admin split. Reparenting an existing
    project requires **Admin** on both sides (:class:`TestReparentGates`, the CVE table),
    and inheriting that rule here is the obvious move and is wrong. Measured on the seed's
    grant ladder:

        parent held as   CREATE      UPDATE
        owner            201         200
        Admin            201         200
        Write            **201**     **403**
        Read             403         403
        nothing          403         403
        missing          404/3001    404/3001

    Nothing guarded create before this. That is worse than a wrong status code, because
    ``delete_project`` recurses into descendants **without re-checking permission**: a
    project attached under somebody else's tree is destroyed when they delete theirs.
    """

    def _grant(self, session: Session, project_id: int, permission: Permission) -> None:
        grant(session, project_id, COLLABORATOR, permission)

    def test_write_on_the_parent_is_enough(self, session: Session) -> None:
        """★ The discriminating cell. Requiring Admin here passes every other test."""
        parent = make_project(session, owner=OWNER, title="theirs")
        self._grant(session, parent.id, Permission.WRITE)

        created = create_project(
            session, owner_id=COLLABORATOR, title="mine", parent_project_id=parent.id
        )

        assert created.parent_project_id == parent.id

    def test_admin_on_the_parent_is_enough(self, session: Session) -> None:
        parent = make_project(session, owner=OWNER, title="theirs")
        self._grant(session, parent.id, Permission.ADMIN)

        assert create_project(
            session, owner_id=COLLABORATOR, title="mine", parent_project_id=parent.id
        ).id

    def test_read_on_the_parent_is_not_enough(self, session: Session) -> None:
        parent = make_project(session, owner=OWNER, title="theirs")
        self._grant(session, parent.id, Permission.READ)

        with pytest.raises(ForbiddenError):
            create_project(
                session, owner_id=COLLABORATOR, title="mine", parent_project_id=parent.id
            )

    def test_no_access_to_the_parent_is_refused(self, session: Session) -> None:
        parent = make_project(session, owner=OWNER, title="theirs")

        with pytest.raises(ForbiddenError):
            create_project(session, owner_id=STRANGER, title="mine", parent_project_id=parent.id)

    def test_the_owner_may_still_nest_under_their_own(self, session: Session) -> None:
        """Without this, "refuse every parent" passes the three above."""
        parent = make_project(session, owner=OWNER, title="mine")

        assert create_project(
            session, owner_id=OWNER, title="child", parent_project_id=parent.id
        ).id

    def test_a_parent_that_does_not_exist_is_404_not_403(self, session: Session) -> None:
        """Existence is decided before permission — measured, and the reverse order is
        indistinguishable on every other row of the matrix."""
        with pytest.raises(CaltonError) as error:
            create_project(session, owner_id=OWNER, title="c", parent_project_id=424242)

        assert error.value.code == 3001

    def test_a_top_level_create_needs_nothing(self, session: Session) -> None:
        assert create_project(session, owner_id=STRANGER, title="top").id


class TestReparentToAMissingProject:
    """A *changed* parent that does not exist is 404/3001, decided before the Admin gate.

    ⚠️ Only the changed branch looks the parent up. Re-sending a stored-but-dangling
    parent must stay an ordinary write — upstream 500s there, which we deliberately do
    not reproduce (registered as
    ``deviation.project_update.dangling_parent_unchanged_is_500``).
    """

    def test_moving_to_a_missing_parent_is_404(self, session: Session) -> None:
        project = make_project(session, title="mover")

        with pytest.raises(CaltonError) as error:
            update_project(
                session, project=project, user_id=OWNER, title="mover", parent_project_id=424242
            )

        assert error.value.code == 3001

    def test_resending_a_dangling_parent_unchanged_is_an_ordinary_write(
        self, session: Session
    ) -> None:
        """★ The cell upstream 500s on. Ours must not 404 — the parent is not looked up."""
        project = make_project(session, title="orphan")
        project.parent_project_id = 424242  # dangling, as seed project 39 is
        session.flush()

        update_project(
            session, project=project, user_id=OWNER, title="renamed", parent_project_id=424242
        )

        assert project.title == "renamed"


class TestArchivedIsInheritedAtReadTime:
    """``is_archived`` is the project's own flag **OR any ancestor's**.

    Upstream does *both* halves — it propagates the column on write (see
    :class:`TestArchiving`) and it inherits on read. That looks redundant until you ask
    where they could disagree: a child created after its parent was archived. Upstream
    closes that input with 412/3008, so the disagreement is unobservable, and each half
    covers a case the other cannot:

    * propagation alone misses seed project 21, whose column is 0 under archived 22 —
      a row upstream's own fixtures ship and upstream reports as archived;
    * inheritance alone would leave the stored column stale, and it *is* observable,
      because the archive endpoint writes it and the seed fixtures depend on it.

    All of it measured in ``harness/probe_coder_e_archived.py``.
    """

    def test_a_child_of_an_archived_parent_reads_as_archived(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        # ⚠️ Archived by writing the column directly, NOT through update_project: that
        # would also run the write-time propagation and set the child's own flag, and
        # then this test would pass with inheritance deleted. The fixture row this
        # reproduces (seed 21) is exactly a child whose own flag was never written.
        parent.is_archived = True
        session.flush()

        assert child.is_archived is False, "the sample is only discriminating while this holds"
        assert reads_as_archived(session, child) is True

    def test_it_reaches_through_more_than_one_generation(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grandchild = make_project(session, title="grandchild", parent=child.id)
        parent.is_archived = True
        session.flush()

        assert reads_as_archived(session, grandchild) is True

    def test_a_live_sibling_is_untouched(self, session: Session) -> None:
        """Without this, "return True always" passes every assertion above."""
        parent = make_project(session, title="parent")
        bystander = make_project(session, title="bystander")
        parent.is_archived = True
        session.flush()

        assert reads_as_archived(session, bystander) is False

    def test_a_parent_cycle_terminates(self, session: Session) -> None:
        """⚠️ Corrupt data must not hang the read path.

        The natural CTE carries a ``level`` column so it can stop at a depth bound. That
        makes every row distinct, so ``UNION`` cannot dedupe and a two-project cycle emits
        each id once per level — measured, 512 copies each. Carrying only the id makes the
        recursion stop when it stops producing new ids, which is a property of the data
        rather than of a constant someone has to keep large enough.
        """
        first = make_project(session, title="a")
        second = make_project(session, title="b", parent=first.id)
        first.parent_project_id = second.id
        second.is_archived = True
        session.flush()

        ids = list(session.scalars(archived_project_ids(session)))

        assert sorted(ids) == sorted({first.id, second.id})
        assert len(ids) == len(set(ids)), f"the walk revisited ids: {ids}"


class TestCreatingUnderAnArchivedProject:
    """412/3008, and it is what keeps the two archive mechanisms consistent."""

    def test_a_directly_archived_parent_is_refused(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        parent.is_archived = True
        session.flush()

        with pytest.raises(CaltonError) as error:
            create_project(session, owner_id=OWNER, title="child", parent_project_id=parent.id)

        assert error.value.code == 3008

    def test_an_inherited_archived_parent_is_refused_too(self, session: Session) -> None:
        """★ The discriminating cell: the parent's own column is 0.

        Testing the stored column instead of the inherited value passes every other case
        here and lets a client create inside a subtree the API reports as archived.
        Measured: upstream answers 412 for a create under seed project 21.
        """
        grandparent = make_project(session, title="grandparent")
        parent = make_project(session, title="parent", parent=grandparent.id)
        grandparent.is_archived = True
        session.flush()
        assert parent.is_archived is False, "the sample is only discriminating while this holds"

        with pytest.raises(CaltonError) as error:
            create_project(session, owner_id=OWNER, title="child", parent_project_id=parent.id)

        assert error.value.code == 3008

    def test_a_live_parent_is_allowed(self, session: Session) -> None:
        parent = make_project(session, title="parent")

        created = create_project(
            session, owner_id=OWNER, title="child", parent_project_id=parent.id
        )

        assert created.id
        assert created.parent_project_id == parent.id

    def test_a_top_level_create_is_allowed_while_something_else_is_archived(
        self, session: Session
    ) -> None:
        """The gate must key off *this* parent, not "is anything archived"."""
        other = make_project(session, title="other")
        other.is_archived = True
        session.flush()

        assert create_project(session, owner_id=OWNER, title="top").id


class TestArchiving:
    """Divergence 2: archiving cascades, and un-archiving is why the whitelist exists."""

    def test_archiving_a_parent_archives_its_descendants(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        grandchild = make_project(session, title="grandchild", parent=child.id)

        update_project(session, project=parent, user_id=OWNER, title="parent", is_archived=True)

        assert parent.is_archived is True
        assert child.is_archived is True
        assert grandchild.is_archived is True

    def test_un_archiving_frees_the_descendants_again(self, session: Session) -> None:
        """The whole reason the column whitelist exists (project.go:1256).

        Deleting ``is_archived`` from the whitelist leaves this stuck archived, which is
        the exact bug the upstream comment describes.
        """
        parent = make_project(session, title="parent")
        child = make_project(session, title="child", parent=parent.id)
        update_project(session, project=parent, user_id=OWNER, title="parent", is_archived=True)
        # Archived for real first, or the un-archive below proves nothing: the cascade is
        # driven by the argument, so a project whose own column is never written still
        # archives its children and would pass the assertions underneath.
        assert parent.is_archived is True

        update_project(session, project=parent, user_id=OWNER, title="parent", is_archived=False)

        assert parent.is_archived is False
        assert child.is_archived is False

    def test_an_unrelated_project_is_not_archived(self, session: Session) -> None:
        parent = make_project(session, title="parent")
        bystander = make_project(session, title="bystander")

        update_project(session, project=parent, user_id=OWNER, title="parent", is_archived=True)

        assert bystander.is_archived is False


class TestWhitelistColumnsAreLoadBearing:
    """B3: every column in the whitelist must be observably written."""

    def test_the_dictionary_is_derived_from_the_constant(self) -> None:
        """So the two cannot drift apart, which a hand-copied dict allows."""
        assert set(UNCONDITIONAL_UPDATE_COLUMNS) == {
            "title",
            "is_archived",
            "identifier",
            "hex_color",
            "position",
        }

    def test_position_is_written_by_an_update(self, session: Session) -> None:
        project = make_project(session)

        update_project(session, project=project, user_id=OWNER, title="p", position=4096.0)

        assert project.position == 4096.0

    def test_an_omitted_position_renumbers_the_siblings(self, session: Session) -> None:
        """Divergence 3: Go's position is a plain float64, so omitting it writes 0.

        A value under 0.1 renumbers the siblings across the position space — but **the
        updated project itself keeps the 0**, because upstream renumbers *before* it
        writes the whitelisted columns (``project.go:1278`` then ``:1303``), so the row
        it just renumbered is immediately overwritten with the position from the request.

        ⚠️ This asserted ``first.position > 0.1`` and passed, which is the natural way to
        write "a 0 does not survive". It is the one project for which a 0 *does* survive,
        and the assertion held only because this code used to renumber last. Measured on
        the reference server: a freshly created project at ``id * 2^16``, updated with
        title alone, comes back at exactly ``position: 0`` while its sibling moves. Arms
        for omitted / explicit 0 / 0.05 / 12345 are in
        ``harness/probe_coder_e_position.py``.
        """
        first = make_project(session, title="a")
        second = make_project(session, title="b")
        assert first.position and second.position, "both start non-zero, or nothing moves"

        update_project(session, project=first, user_id=OWNER, title="a")

        assert first.position == 0
        # Two siblings, ordered by position ascending: the second one lands on 2^32.
        assert second.position == float(2**32)

    def test_a_top_level_project_stored_as_null_is_not_renumbered(self, session: Session) -> None:
        """⚠️ NULL is not 0 here, and the whole renumbering hinges on it.

        ``recalculateProjectPositions`` selects with a bare
        ``Where("parent_project_id = ?", 0)`` and SQL equality never matches NULL, so a
        top-level project stored as NULL — which is every one of the 42 in the seed —
        takes no part. Upstream's own create writes integer 0, which is why projects it
        creates *are* renumbered while seeded ones never move.

        Without this, matching NULL as well looks like an obvious correctness fix ("both
        mean no parent"), and it silently renumbers 12 seeded projects on a title-only
        update that upstream leaves alone.
        """
        untouched = make_project(session, title="seeded-style")
        untouched.parent_project_id = None
        session.flush()
        before = untouched.position

        subject = make_project(session, title="api-created")
        update_project(session, project=subject, user_id=OWNER, title="api-created")

        assert untouched.position == before


class TestHexColour:
    """Divergence 1: NormalizeHex strips the leading # and keeps six characters."""

    def test_a_leading_hash_is_stripped_on_create(self, session: Session) -> None:
        project = create_project(session, owner_id=OWNER, title="p", hex_color="#ff0000")

        assert project.hex_color == "ff0000"

    def test_a_leading_hash_is_stripped_on_update(self, session: Session) -> None:
        project = make_project(session)

        update_project(session, project=project, user_id=OWNER, title="p", hex_color="#00ff00")

        assert project.hex_color == "00ff00"

    def test_longer_input_is_truncated_to_six(self, session: Session) -> None:
        project = create_project(session, owner_id=OWNER, title="p", hex_color="#ff0000ff")

        assert project.hex_color == "ff0000"


class TestPseudoParents:
    """Divergence 5: a negative parent addresses a pseudo project, which owns nothing."""

    def test_a_negative_parent_is_refused_on_create(self, session: Session) -> None:
        with pytest.raises(CaltonError) as excinfo:
            create_project(session, owner_id=OWNER, title="p", parent_project_id=-1)

        assert excinfo.value.code == 3009

    def test_a_negative_parent_is_refused_on_update(self, session: Session) -> None:
        project = make_project(session)

        with pytest.raises(CaltonError) as excinfo:
            update_project(session, project=project, user_id=OWNER, title="p", parent_project_id=-1)

        assert excinfo.value.code == 3009


class TestIdentifierUniqueness:
    """Divergence 4: 3007 was registered and nothing raised it."""

    def test_a_duplicate_identifier_is_refused(self, session: Session) -> None:
        create_project(session, owner_id=OWNER, title="a", identifier="ABC")

        with pytest.raises(CaltonError) as excinfo:
            create_project(session, owner_id=OWNER, title="b", identifier="ABC")

        assert excinfo.value.code == 3007

    def test_the_comparison_ignores_case(self, session: Session) -> None:
        """Identifiers are stored uppercase, so 'abc' collides with 'ABC'."""
        create_project(session, owner_id=OWNER, title="a", identifier="ABC")

        with pytest.raises(CaltonError):
            create_project(session, owner_id=OWNER, title="b", identifier="abc")

    def test_a_project_may_keep_its_own_identifier(self, session: Session) -> None:
        """Otherwise every update of an identified project would 3007 against itself."""
        project = create_project(session, owner_id=OWNER, title="a", identifier="ABC")

        update_project(session, project=project, user_id=OWNER, title="a", identifier="ABC")

        assert project.identifier == "ABC"


class TestValidationOrder:
    """Divergence 6: the hierarchy is checked before the permission gates."""

    def test_a_cyclic_move_by_a_non_admin_reports_the_cycle_not_a_403(
        self, session: Session
    ) -> None:
        """Upstream validates first, so the client learns the move is impossible.

        With the order reversed this answers 403, which tells the user to go ask for
        permissions that would not help.
        """
        grandparent = make_project(session, title="gp")
        parent = make_project(session, title="p", parent=grandparent.id)
        grant(session, grandparent.id, COLLABORATOR, Permission.WRITE)

        with pytest.raises(CaltonError) as excinfo:
            update_project(
                session,
                project=grandparent,
                user_id=COLLABORATOR,
                title="gp",
                parent_project_id=parent.id,
            )

        assert excinfo.value.code == 3011


class TestDeleteRemovesRelatedRows:
    """Divergence 7: shares and grants go with the project."""

    def test_project_grants_are_removed(self, session: Session) -> None:
        project = make_project(session)
        grant(session, project.id, COLLABORATOR, Permission.WRITE)

        delete_project(session, project=project)

        assert list(session.scalars(select(ProjectUser))) == []

    def test_team_grants_are_removed(self, session: Session) -> None:
        project = make_project(session)
        session.add(TeamProject(project_id=project.id, team_id=1, permission=1))
        session.flush()

        delete_project(session, project=project)

        assert list(session.scalars(select(TeamProject))) == []


class TestDefaultProject:
    """A user's default project is where new tasks land, so it is protected."""

    def _make_default(self, session: Session, owner: int = OWNER) -> Project:
        project = make_project(session, owner=owner)
        user = session.get(User, owner)
        assert user is not None
        user.default_project_id = project.id
        session.flush()
        return project

    def test_archiving_the_default_project_is_refused(self, session: Session) -> None:
        project = self._make_default(session)

        with pytest.raises(CaltonError) as excinfo:
            update_project(session, project=project, user_id=OWNER, title="p", is_archived=True)

        assert excinfo.value.code == 3013

    def test_an_ordinary_project_can_still_be_archived(self, session: Session) -> None:
        """The control: the refusal is about being someone's default, not about archiving."""
        self._make_default(session)
        other = make_project(session, title="other")

        update_project(session, project=other, user_id=OWNER, title="other", is_archived=True)

        assert other.is_archived is True

    def test_a_non_owner_cannot_delete_the_default_project(self, session: Session) -> None:
        project = self._make_default(session)

        with pytest.raises(CaltonError) as excinfo:
            delete_project(session, project=project, user_id=STRANGER)

        assert excinfo.value.code == 3012

    def test_the_owner_may_delete_their_own_default_project(self, session: Session) -> None:
        project = self._make_default(session)
        project_id = project.id

        delete_project(session, project=project, user_id=OWNER)

        assert session.get(Project, project_id) is None

    def test_a_descendant_that_is_someone_elses_default_still_goes(self, session: Session) -> None:
        """The rule guards the top of the call, not the recursion — as upstream does."""
        parent = make_project(session, title="parent")
        child = nest_under(session, make_project(session, title="child", owner=STRANGER), parent)
        stranger = session.get(User, STRANGER)
        assert stranger is not None
        stranger.default_project_id = child.id
        session.flush()
        child_id = child.id

        delete_project(session, project=parent, user_id=OWNER)

        assert session.get(Project, child_id) is None


class TestDeleteRemovesFavorites:
    def test_a_favourite_entry_for_the_project_is_removed(self, session: Session) -> None:
        project = make_project(session)
        session.add(Favorite(entity_id=project.id, user_id=OWNER, kind=FAVORITE_KIND_PROJECT))
        session.flush()

        delete_project(session, project=project)

        assert list(session.scalars(select(Favorite))) == []

    def test_a_task_favourite_with_the_same_id_survives(self, session: Session) -> None:
        """The kind is part of the key, so filtering on entity_id alone is not enough."""
        project = make_project(session)
        session.add(Favorite(entity_id=project.id, user_id=OWNER, kind=1))
        session.flush()

        delete_project(session, project=project)

        assert len(list(session.scalars(select(Favorite)))) == 1
