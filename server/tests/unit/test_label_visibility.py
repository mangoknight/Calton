"""Label visibility, the three-way rule.

Ported from the measured corpus (``harness/corpus-incoming/corpus/_labels.yaml``), which
was recorded against the running Go server. The split is read/use versus edit/delete, not
mine versus someone else's, and both ways of getting it wrong are silent:

* narrowing "use" to the creator breaks shared labels — the picker offers them and the
  attach 403s
* widening "edit" to anyone who can see it lets a user rename another user's label

so each direction has its own case rather than relying on one test to cover both.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.models import Label, LabelTask, Project, ProjectUser, Task, User
from calton.permissions.project import Permission
from calton.services.label_service import (
    can_modify_label,
    can_read_label,
    visible_labels_query,
)

ALICE, BOB = 900, 901


@pytest.fixture
def session() -> Iterator[Session]:
    """Mirrors the corpus seed: two users, a shared project and a private one."""
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)

    with session_factory(engine)() as opened:
        opened.add_all(
            [
                User(id=ALICE, username="alice"),
                User(id=BOB, username="bob"),
                # Alice owns 950; Bob has write on it, so tasks there are visible to both.
                Project(id=950, title="shared", owner_id=ALICE),
                ProjectUser(id=1, project_id=950, user_id=BOB, permission=Permission.WRITE),
                # Bob's own project, which Alice cannot see.
                Project(id=927, title="bob private", owner_id=BOB),
                Task(id=950, title="shared task", project_id=950, index=1, created_by_id=ALICE),
                Task(id=927, title="bob task", project_id=927, index=1, created_by_id=BOB),
                # 950, 951: Alice's, attached to the shared task.
                Label(id=950, title="a1", created_by_id=ALICE),
                Label(id=951, title="a2", created_by_id=ALICE),
                # 952: Alice's, attached to nothing.
                Label(id=952, title="a3 floating", created_by_id=ALICE),
                # 953: Bob's, only on his private task.
                Label(id=953, title="b1", created_by_id=BOB),
                # 954: Bob's, attached to Alice's shared task.
                Label(id=954, title="b2 shared", created_by_id=BOB),
                LabelTask(id=1, task_id=950, label_id=950),
                LabelTask(id=2, task_id=950, label_id=951),
                LabelTask(id=3, task_id=950, label_id=954),
                LabelTask(id=4, task_id=927, label_id=953),
            ]
        )
        opened.commit()
        yield opened


def _visible(session: Session, user_id: int) -> list[int]:
    return [label.id for label in session.scalars(visible_labels_query(session, user_id))]


class TestVisibleSet:
    def test_alice_sees_her_own_plus_bobs_on_her_task(self, session: Session) -> None:
        """Corpus ``label.read_all.ok``. Each id blocks a different wrong implementation.

        954 is Bob's but attached to Alice's task, so "created_by = me" misses it. 952 is
        floating, so "on a task I can see" misses it. 953 is Bob's on his own private
        task, so anything too broad picks it up.
        """
        assert _visible(session, ALICE) == [950, 951, 952, 954]

    def test_bob_sees_alices_labels_through_the_shared_task(self, session: Session) -> None:
        """Corpus ``label.read_all.other_user_sees_via_shared_task``.

        The symmetric half. Implementing visibility as "created_by = me" leaves Alice's
        case green and only breaks this one, which is why both exist.
        """
        assert _visible(session, BOB) == [950, 951, 953, 954]

    def test_a_private_label_does_not_leak(self, session: Session) -> None:
        assert 953 not in _visible(session, ALICE)

    def test_a_floating_label_is_visible_to_its_creator(self, session: Session) -> None:
        assert 952 in _visible(session, ALICE)
        assert 952 not in _visible(session, BOB)


class TestUseIsNotRestrictedToTheCreator:
    """Corpus ``tasklabel.add.readable_others_label_ok``."""

    def test_a_visible_label_of_another_user_may_be_read(self, session: Session) -> None:
        assert can_read_label(session, ALICE, 954)

    def test_an_invisible_label_may_not(self, session: Session) -> None:
        assert not can_read_label(session, ALICE, 953)

    def test_visibility_does_not_require_authorship(self, session: Session) -> None:
        """The rule this module exists for: visible is usable."""
        label = session.get(Label, 954)
        assert label is not None and label.created_by_id != ALICE
        assert can_read_label(session, ALICE, 954)


class TestModifyIsCreatorOnly:
    """Corpus ``label.update.other_owner_403`` / ``label.delete.other_owner_403``."""

    def test_the_creator_may_modify(self, session: Session) -> None:
        assert can_modify_label(session, ALICE, 950)

    def test_someone_who_can_only_see_it_may_not(self, session: Session) -> None:
        """Bob sees 950 through the shared task but must not be able to rename it."""
        assert can_read_label(session, BOB, 950)
        assert not can_modify_label(session, BOB, 950)

    def test_the_two_permissions_are_genuinely_different(self, session: Session) -> None:
        """If read and modify ever collapse into one check, this is what notices."""
        readable = {
            label_id
            for label_id in (950, 951, 952, 953, 954)
            if can_read_label(session, BOB, label_id)
        }
        modifiable = {
            label_id
            for label_id in (950, 951, 952, 953, 954)
            if can_modify_label(session, BOB, label_id)
        }

        assert modifiable < readable
        assert modifiable == {953, 954}


class TestMissingLabels:
    def test_a_missing_label_is_not_readable(self, session: Session) -> None:
        assert not can_read_label(session, ALICE, 9999)

    def test_a_missing_label_is_not_modifiable(self, session: Session) -> None:
        assert not can_modify_label(session, ALICE, 9999)

    def test_label_id_zero_is_not_readable(self, session: Session) -> None:
        """Corpus ``tasklabel.add.label_id_zero_is_403_not_404``: an omitted label_id is
        0, which must reach the permission check and be refused there — not be rejected
        earlier as a validation error, and not be looked up as an ordinary id."""
        assert not can_read_label(session, ALICE, 0)
