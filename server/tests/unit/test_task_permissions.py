"""Task permissions come entirely from the task's project.

The cases worth having are the ones where "delegate to the project" is not the whole
story: a soft-deleted task must be invisible, and moving a task consults two projects.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.models import Project, ProjectUser, Task, User
from calton.permissions.project import Permission
from calton.permissions.task import can_move, can_read, can_write

READ, WRITE, ADMIN = Permission.READ, Permission.WRITE, Permission.ADMIN


@pytest.fixture
def session() -> Iterator[Session]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as opened:
        opened.add_all(
            [
                User(id=1, username="alice"),
                User(id=2, username="bob"),
                Project(id=10, title="alice's", owner_id=1),
                Project(id=11, title="bob's", owner_id=2),
                Task(id=100, title="t", project_id=10, index=1, created_by_id=1),
                Task(
                    id=101,
                    title="deleted",
                    project_id=10,
                    index=2,
                    created_by_id=1,
                    deleted_at=datetime(2026, 8, 3, tzinfo=UTC),
                ),
            ]
        )
        opened.commit()
        yield opened


class TestDelegation:
    def test_the_project_owner_can_read_and_write(self, session: Session) -> None:
        assert can_read(session, 1, 100) == (True, ADMIN)
        assert can_write(session, 1, 100)

    def test_a_stranger_can_do_neither(self, session: Session) -> None:
        assert can_read(session, 2, 100) == (False, 0)
        assert not can_write(session, 2, 100)

    def test_read_access_to_the_project_is_not_write_access(self, session: Session) -> None:
        session.add(ProjectUser(id=1, project_id=10, user_id=2, permission=READ))
        session.commit()

        assert can_read(session, 2, 100) == (True, READ)
        assert not can_write(session, 2, 100)

    def test_write_access_carries_through(self, session: Session) -> None:
        session.add(ProjectUser(id=1, project_id=10, user_id=2, permission=WRITE))
        session.commit()

        assert can_write(session, 2, 100)

    def test_inherited_project_access_reaches_tasks(self, session: Session) -> None:
        session.add(Project(id=12, title="child", owner_id=1, parent_project_id=10))
        session.add(Task(id=200, title="t", project_id=12, index=1, created_by_id=1))
        session.add(ProjectUser(id=1, project_id=10, user_id=2, permission=WRITE))
        session.commit()

        assert can_write(session, 2, 200)


class TestMissingAndDeleted:
    def test_a_missing_task_is_denied(self, session: Session) -> None:
        assert can_read(session, 1, 9999) == (False, 0)
        assert not can_write(session, 1, 9999)

    def test_a_soft_deleted_task_is_denied_even_to_its_owner(self, session: Session) -> None:
        """Resolving permissions for a deleted task would let it be read or edited; the
        row is still there, so only the soft-delete filter prevents it."""
        assert can_read(session, 1, 101) == (False, 0)
        assert not can_write(session, 1, 101)


class TestMoving:
    def test_moving_needs_write_on_both_sides(self, session: Session) -> None:
        assert not can_move(session, 1, 100, 11)

    def test_moving_within_reach_is_allowed(self, session: Session) -> None:
        session.add(ProjectUser(id=1, project_id=11, user_id=1, permission=WRITE))
        session.commit()

        assert can_move(session, 1, 100, 11)

    def test_write_on_the_destination_alone_is_not_enough(self, session: Session) -> None:
        """Otherwise a user could move tasks out of a project they cannot write to."""
        session.add(ProjectUser(id=1, project_id=11, user_id=2, permission=ADMIN))
        session.commit()

        assert not can_move(session, 2, 100, 11)

    def test_moving_to_a_missing_project_is_denied(self, session: Session) -> None:
        assert not can_move(session, 1, 100, 9999)
