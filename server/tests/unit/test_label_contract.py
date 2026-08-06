"""Label behaviours that look like bugs and are the requirement.

Every case here exists because the *tidier* implementation is the wrong one. They are
written as negative acceptance criteria: adding the validation, or hydrating the
response, or short-circuiting the empty case, turns them red on purpose.

All expectations come from the measured corpus
(``harness/corpus-incoming/corpus/_labels.yaml``), recorded against the running Go server.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.core.policy import ForbiddenError
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.db.types import ZERO_TIME
from calton.models import Label, LabelTask, Project, Task, User
from calton.schemas.label import LabelBulk, LabelReference, LabelWrite
from calton.services.label_service import (
    LabelDoesNotExistError,
    is_attached,
    labels_on_task,
    load_for_read,
    load_for_write,
    replace_task_labels,
)

ALICE = 900


@pytest.fixture
def session() -> Iterator[Session]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)

    with session_factory(engine)() as opened:
        opened.add_all(
            [
                User(id=ALICE, username="alice"),
                Project(id=950, title="p", owner_id=ALICE),
                Task(id=950, title="t", project_id=950, index=1, created_by_id=ALICE),
                Label(id=950, title="one", created_by_id=ALICE),
                Label(id=951, title="two", created_by_id=ALICE),
                Label(id=952, title="three", created_by_id=ALICE),
                Label(id=954, title="four", created_by_id=ALICE),
                LabelTask(id=1, task_id=950, label_id=950),
                LabelTask(id=2, task_id=950, label_id=951),
                LabelTask(id=3, task_id=950, label_id=954),
            ]
        )
        opened.commit()
        yield opened


class TestCreateHasNoValidation:
    """Corpus ``label.create.empty_title_is_accepted`` / ``empty_body_is_accepted``.

    ``PUT /labels`` accepts an empty title and an empty body, answering 201 where projects
    and tasks answer 400. **The absence of validation is the requirement.** Adding a
    ``min_length`` or marking ``title`` required looks like fixing an oversight and is the
    change these tests exist to stop.
    """

    def test_an_empty_title_is_accepted(self) -> None:
        assert LabelWrite.model_validate({"title": ""}).title == ""

    def test_an_empty_body_is_accepted(self) -> None:
        assert LabelWrite.model_validate({}).title == ""

    def test_a_whitespace_only_title_is_accepted_unchanged(self) -> None:
        """Upstream's "required" means non-zero, not non-blank.

        A ``strip()`` before the emptiness check would reject input the reference server
        takes, and would also alter what gets stored.
        """
        assert LabelWrite.model_validate({"title": "   "}).title == "   "

    def test_no_field_is_required(self) -> None:
        """Stated as a property rather than field by field, so a new required field on
        this model fails here rather than in the harness."""
        assert not [name for name, field in LabelWrite.model_fields.items() if field.is_required()]


class TestBulkIsFullReplacement:
    """Corpus ``tasklabel.bulk.replaces_whole_set`` / ``empty_clears_all``."""

    def test_it_replaces_rather_than_appends(self, session: Session) -> None:
        """Starting {950, 951, 954} and submitting [952, 950] leaves exactly {950, 952}.

        An appending implementation returns the same status and leaves {950, 951, 952,
        954}, so only the resulting set distinguishes them — and the starting set must
        contain something absent from the submission, which 951 and 954 provide.
        """
        replace_task_labels(session, 950, [952, 950])

        assert [label.id for label in labels_on_task(session, 950)] == [950, 952]

    def test_an_empty_list_clears_everything(self, session: Session) -> None:
        """The dangerous case: short-circuiting on empty leaves the response body correct
        while silently doing nothing, so this checks the database rather than the reply."""
        replace_task_labels(session, 950, [])

        assert labels_on_task(session, 950) == []

    def test_replacing_with_the_same_set_is_stable(self, session: Session) -> None:
        replace_task_labels(session, 950, [950, 951, 954])

        assert [label.id for label in labels_on_task(session, 950)] == [950, 951, 954]

    def test_it_does_not_disturb_other_tasks(self, session: Session) -> None:
        session.add(Task(id=951, title="other", project_id=950, index=2, created_by_id=ALICE))
        session.add(LabelTask(id=4, task_id=951, label_id=952))
        session.commit()

        replace_task_labels(session, 950, [])

        assert is_attached(session, 951, 952)


class TestBulkResponseIsAnUnhydratedEcho:
    """Corpus ``tasklabel.bulk.response_echoes_input_unhydrated``.

    The 201 body repeats the request, not the database: empty titles, null
    ``created_by``, zero timestamps, in the submitted order. Hydrating it produces a
    more useful reply that no longer matches upstream byte for byte.
    """

    def test_the_echo_keeps_request_order(self) -> None:
        """Submitted [952, 950]; ``GET`` would answer [950, 952]. The echo is not sorted."""
        bulk = LabelBulk(labels=[LabelReference(id=952), LabelReference(id=950)])

        assert [entry.id for entry in bulk.labels] == [952, 950]

    def test_the_echoed_entries_are_not_hydrated(self) -> None:
        entry = LabelBulk(labels=[LabelReference(id=952)]).labels[0]

        assert entry.title == ""
        assert entry.description == ""
        assert entry.created_by is None
        assert entry.created == ZERO_TIME

    def test_an_echoed_entry_serializes_with_the_zero_timestamp(self) -> None:
        dumped = LabelReference(id=952).model_dump(mode="json")

        assert dumped["created"] == "0001-01-01T00:00:00Z"
        assert dumped["created_by"] is None

    def test_an_empty_bulk_serializes_as_an_empty_list(self) -> None:
        assert LabelBulk().model_dump(mode="json") == {"labels": []}


class TestNotFoundIsAsymmetric:
    """Corpus: ``GET /labels/9999`` is 403, ``POST``/``DELETE /labels/9999`` are 404/8002.

    Two paths, two answers, for the same missing label. Harmonising them either way turns
    half the corpus red — and the read side is the one with a security reason: 404 there
    would let a caller enumerate which label ids exist.
    """

    def test_reading_a_missing_label_is_forbidden_not_missing(self, session: Session) -> None:
        with pytest.raises(ForbiddenError):
            load_for_read(session, ALICE, 9999)

    def test_reading_an_invisible_label_is_indistinguishable_from_missing(
        self, session: Session
    ) -> None:
        """The property that makes the 403 worth having: both raise the same thing."""
        session.add(User(id=901, username="bob"))
        session.add(Label(id=960, title="bob's", created_by_id=901))
        session.commit()

        with pytest.raises(ForbiddenError):
            load_for_read(session, ALICE, 960)
        with pytest.raises(ForbiddenError):
            load_for_read(session, ALICE, 9999)

    def test_writing_a_missing_label_reports_missing(self, session: Session) -> None:
        with pytest.raises(LabelDoesNotExistError) as raised:
            load_for_write(session, ALICE, 9999)

        assert raised.value.code == 8002
        assert raised.value.http_status == 404

    def test_writing_someone_elses_label_is_forbidden(self, session: Session) -> None:
        """Missing is checked before ownership, so these two differ."""
        session.add(User(id=901, username="bob"))
        session.add(Label(id=960, title="bob's", created_by_id=901))
        session.commit()

        with pytest.raises(ForbiddenError):
            load_for_write(session, ALICE, 960)

    def test_the_owner_may_load_for_write(self, session: Session) -> None:
        assert load_for_write(session, ALICE, 950).id == 950

    def test_reading_and_writing_disagree_on_the_same_id(self, session: Session) -> None:
        """Stated directly, so collapsing the two paths into one helper fails here."""
        with pytest.raises(ForbiddenError):
            load_for_read(session, ALICE, 9999)
        with pytest.raises(LabelDoesNotExistError):
            load_for_write(session, ALICE, 9999)
