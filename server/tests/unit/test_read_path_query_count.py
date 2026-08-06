"""The read path must not issue a query per task, per bucket, or per related task.

Every performance assertion here is written as a **slope**, not as an absolute budget:
measure the same endpoint at two sizes and require the difference to stay flat. An
absolute number ("no more than 40 statements") passes the day someone halves a constant
overhead while leaving the per-task query in place, and it has to be re-tuned every time
an unrelated column is added. The slope is the property that actually matters and it is
stable under both.

⚠️ The counter observes ``before_cursor_execute``, so it counts **statements sent to the
driver**. A row served out of SQLAlchemy's identity map issues no cursor execute and is
invisible here — which is the right thing to count, since it is also what the database
sees.

The permission cases in :class:`TestRelatedTasksPermissionSurvivesBatching` are not about
speed. ``related_tasks`` renders the title, description and dates of the far end, so the
filter that drops an unreadable one is the only thing between this endpoint and reading
any task in the database by id. Batching relations across a page is exactly the refactor
that loses it: the far ends arrive as one ``IN (...)`` set detached from the task they
belong to, and permission is a property of the far end's *project*.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB, BOBS_PROJECT

from calton.models import Bucket, Project, ProjectView, Task, TaskBucket, TaskPosition
from calton.models.task_assignee import TaskAssignee
from calton.models.task_relation import TaskRelation

#: Ids used only by this module, so a change to the shared fixtures cannot silently
#: change what is being counted.
FLAT_PROJECT = 990
#: Two boards holding the *same* number of tasks in a different number of columns. Both
#: are seeded up front rather than one being torn down and rebuilt, because a delete pass
#: between the two measurements is itself SQL and would land in the second count.
WIDE_PROJECT = 991
WIDE_VIEW = 9910
NARROW_PROJECT = 992
NARROW_VIEW = 9920


class StatementCounter:
    """Counts statements handed to the DBAPI, keeping the SQL for failure messages."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __len__(self) -> int:
        return len(self.statements)

    def summary(self, limit: int = 6) -> str:
        """The most frequent statement shapes — enough to name the offender on failure."""
        seen: dict[str, int] = {}
        for statement in self.statements:
            head = " ".join(statement.split())[:110]
            seen[head] = seen.get(head, 0) + 1
        ranked = sorted(seen.items(), key=lambda item: -item[1])[:limit]
        return "\n".join(f"  {count:>4}x {head}" for head, count in ranked)


@contextmanager
def counting(engine: Engine) -> Iterator[StatementCounter]:
    counter = StatementCounter()

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _add_task(session: Session, task_id: int, project_id: int, index: int) -> None:
    session.add(
        Task(
            id=task_id,
            project_id=project_id,
            index=index,
            title=f"C-{task_id}",
            created_by_id=ALICE,
            done=False,
            created=datetime(2026, 2, 1, tzinfo=UTC),
            updated=datetime(2026, 2, 1, tzinfo=UTC),
        )
    )
    session.flush()
    # An assignee on every task: an empty collection short-circuits before the per-row
    # work, so a page of bare tasks would measure a read path production never serves.
    session.add(TaskAssignee(task_id=task_id, user_id=ALICE))


@pytest.fixture
def flat_seed(sessions: sessionmaker[Session]) -> None:
    """25 tasks in one project, each with an assignee and a relation to the first."""
    with sessions() as session:
        session.add(Project(id=FLAT_PROJECT, title="Counted", identifier="CN", owner_id=ALICE))
        session.flush()
        for offset in range(25):
            _add_task(session, 99000 + offset, FLAT_PROJECT, offset + 1)
        # Every task relates to the first one, so the *set* of far ends stays at one while
        # the relation count grows with the page. That is what separates "one query per
        # relation" from "one query for all far ends".
        for offset in range(1, 25):
            session.add(
                TaskRelation(
                    task_id=99000 + offset,
                    other_task_id=99000,
                    relation_kind="related",
                    created_by_id=ALICE,
                )
            )
        session.commit()


def _seed_board(
    session: Session, project_id: int, view_id: int, bucket_count: int, per_bucket: int
) -> None:
    session.add(Project(id=project_id, title=f"Board-{project_id}", identifier="", owner_id=ALICE))
    session.add(
        ProjectView(
            id=view_id,
            project_id=project_id,
            title="Kanban",
            view_kind=3,
            position=100,
            bucket_configuration_mode=1,
        )
    )
    session.flush()
    for bucket_index in range(bucket_count):
        bucket_id = view_id * 10 + bucket_index
        session.add(
            Bucket(
                id=bucket_id,
                project_view_id=view_id,
                title=f"B-{bucket_index}",
                position=100.0 * (bucket_index + 1),
                created_by_id=ALICE,
                limit=0,
            )
        )
        session.flush()
        for slot in range(per_bucket):
            task_id = view_id * 100 + bucket_index * 10 + slot
            _add_task(session, task_id, project_id, bucket_index * 10 + slot + 1)
            session.add(TaskBucket(task_id=task_id, bucket_id=bucket_id, project_view_id=view_id))
            session.add(
                TaskPosition(task_id=task_id, project_view_id=view_id, position=float(slot))
            )


@pytest.fixture
def board_seed(sessions: sessionmaker[Session]) -> None:
    """Two boards, 20 tasks each: one in 2 columns, one in 10."""
    with sessions() as session:
        _seed_board(session, NARROW_PROJECT, NARROW_VIEW, bucket_count=2, per_bucket=10)
        _seed_board(session, WIDE_PROJECT, WIDE_VIEW, bucket_count=10, per_bucket=2)
        session.commit()


class TestFlatCollectionDoesNotQueryPerTask:
    def test_statement_count_is_flat_in_page_size(
        self, flat_seed: None, engine: Engine, client: TestClient
    ) -> None:
        """Five tasks and twenty-five tasks must cost about the same number of statements.

        Not *exactly* the same: a wider page legitimately fans out a little in places that
        are batched but bounded by distinct projects or users rather than by tasks. The
        bound is far below what an un-batched path produces here — 20 extra tasks at
        roughly six queries each.
        """
        with counting(engine) as small:
            response = client.get(f"/api/v1/projects/{FLAT_PROJECT}/tasks?per_page=5")
        assert response.status_code == 200, response.text
        assert len(response.json()) == 5

        with counting(engine) as large:
            response = client.get(f"/api/v1/projects/{FLAT_PROJECT}/tasks?per_page=25")
        assert response.status_code == 200, response.text
        assert len(response.json()) == 25

        slope = len(large) - len(small)
        assert slope <= 5, (
            f"20 extra tasks cost {slope} extra statements "
            f"({len(small)} -> {len(large)}); the read path still queries per task.\n"
            f"statements at per_page=25:\n{large.summary()}"
        )


class TestBoardDoesNotMultiplyByBucketCount:
    def test_statement_count_is_flat_in_bucket_count(
        self, board_seed: None, engine: Engine, client: TestClient
    ) -> None:
        """A ten-column board costs no more per task than a two-column one.

        This is the second factor: the board path serialises each bucket separately, so a
        per-task query is multiplied by the column count as well. Holding the *total* task
        count equal between the two boards isolates that factor — if the cost tracked
        tasks alone, both boards would measure the same and the test would pass without
        proving anything.
        """
        with counting(engine) as narrow:
            response = client.get(
                f"/api/v1/projects/{NARROW_PROJECT}/views/{NARROW_VIEW}/tasks?per_page=100"
            )
        assert response.status_code == 200, response.text
        assert [len(entry["tasks"]) for entry in response.json()] == [10, 10]

        with counting(engine) as wide:
            response = client.get(
                f"/api/v1/projects/{WIDE_PROJECT}/views/{WIDE_VIEW}/tasks?per_page=100"
            )
        assert response.status_code == 200, response.text
        assert [len(entry["tasks"]) for entry in response.json()] == [2] * 10

        # Eight extra columns inherently cost eight more bucket windows plus their counts
        # — that shape is upstream's, not ours. What they must not cost is eight more
        # serialisation fan-outs on top.
        slope = len(wide) - len(narrow)
        assert slope <= 20, (
            f"8 extra columns holding the same 20 tasks cost {slope} extra statements "
            f"({len(narrow)} -> {len(wide)}); serialisation is still per-bucket.\n"
            f"statements on the ten-column board:\n{wide.summary()}"
        )


@pytest.fixture
def client_as_bob(app: FastAPI) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(BOB)}, raise_server_exceptions=False)


class TestRelatedTasksPermissionSurvivesBatching:
    """The far-end permission filter, asserted on the *collection* path.

    ``test_relations.py`` already covers the single-task read. That is not enough: the
    collection path is where the batching lives, and the two only share code for as long
    as nobody splits them. A filter present on one and absent on the other reads
    identically in review — both files are green — which is how it was lost once already
    (see the ``task_hydration`` module docstring).
    """

    def test_collection_drops_a_relation_whose_far_end_is_unreadable(
        self, sessions: sessionmaker[Session], client: TestClient
    ) -> None:
        with sessions() as session:
            session.add(
                TaskRelation(
                    task_id=920, other_task_id=927, relation_kind="related", created_by_id=ALICE
                )
            )
            session.commit()

        response = client.get("/api/v1/projects/920/tasks")
        assert response.status_code == 200, response.text

        task = next(entry for entry in response.json() if entry["id"] == 920)
        assert task["related_tasks"] == {}, (
            f"the collection leaked a task Alice cannot read: {task['related_tasks']}"
        )

    def test_the_far_end_is_returned_when_it_is_readable(
        self, sessions: sessionmaker[Session], client: TestClient
    ) -> None:
        """The case above passes trivially if relations are dropped altogether.

        Without this, deleting the whole relation lookup — filter and all — leaves the
        suite green, and "no relations, ever" is indistinguishable from "the filter works".
        """
        with sessions() as session:
            session.add(
                TaskRelation(
                    task_id=920, other_task_id=922, relation_kind="related", created_by_id=ALICE
                )
            )
            session.commit()

        response = client.get("/api/v1/projects/920/tasks")
        assert response.status_code == 200, response.text

        task = next(entry for entry in response.json() if entry["id"] == 920)
        assert [entry["id"] for entry in task["related_tasks"]["related"]] == [922]

    def test_a_page_mixing_readable_and_unreadable_far_ends_keeps_only_the_readable(
        self, sessions: sessionmaker[Session], client: TestClient
    ) -> None:
        """Both kinds on the same page, which is what a batched lookup actually sees.

        With one far end per page a batched implementation can get the right answer by
        applying a single verdict to the whole set. Here the two verdicts differ inside one
        ``IN (...)``, so the filter has to be per far end.
        """
        with sessions() as session:
            session.add_all(
                [
                    TaskRelation(
                        task_id=920, other_task_id=927, relation_kind="related", created_by_id=ALICE
                    ),
                    TaskRelation(
                        task_id=922, other_task_id=923, relation_kind="related", created_by_id=ALICE
                    ),
                ]
            )
            session.commit()

        response = client.get("/api/v1/projects/920/tasks")
        assert response.status_code == 200, response.text
        body = {entry["id"]: entry for entry in response.json()}

        assert body[920]["related_tasks"] == {}
        assert [entry["id"] for entry in body[922]["related_tasks"]["related"]] == [923]

    def test_bob_reading_his_own_project_still_sees_his_own_far_end(
        self, sessions: sessionmaker[Session], client_as_bob: TestClient
    ) -> None:
        """The filter is per caller, not a blanket "hide cross-project relations".

        An implementation that batches relations and then drops every far end outside the
        *page's* project passes the first case for the wrong reason; this one holds it to
        the actual rule.
        """
        with sessions() as session:
            session.add(
                Task(
                    id=928,
                    project_id=BOBS_PROJECT,
                    index=2,
                    title="T-bobs-second",
                    created_by_id=BOB,
                    done=False,
                )
            )
            session.flush()
            session.add(
                TaskRelation(
                    task_id=927, other_task_id=928, relation_kind="related", created_by_id=BOB
                )
            )
            session.commit()

        response = client_as_bob.get(f"/api/v1/projects/{BOBS_PROJECT}/tasks")
        assert response.status_code == 200, response.text

        task = next(entry for entry in response.json() if entry["id"] == 927)
        assert [entry["id"] for entry in task["related_tasks"]["related"]] == [928]


class TestTheBatchedPathRendersWhatTheSingleReadRenders:
    """One task, read two ways, must serialise to the same bytes.

    Every existing shape assertion for ``assignees``, ``labels`` and ``related_tasks``
    lives in ``test_assignees.py`` / ``test_relations.py`` and reads
    ``GET /api/v1/tasks/{id}`` — the *un*-batched route. So the rules those files pin
    (insertion order rather than id order, alphabetical relation keys, the nested far end
    left deliberately unhydrated) were pinned only on the path that does not batch, and a
    batch that got any of them wrong would leave both files green.

    Asserting equality against the single read, rather than restating each rule here, is
    deliberate: a restatement is a second copy of the specification and would drift from
    ``test_relations.py`` the same way the implementations would. This says only "the two
    paths agree", and lets the other files stay the place where *what* they agree on is
    written down.
    """

    @pytest.fixture
    def richly_shaped_task(self, sessions: sessionmaker[Session]) -> None:
        """Task 920 with the orderings that tell a correct batch from a plausible one.

        Bob is assigned *before* Alice although his id is higher, and label 941 is linked
        before 940 although its id is higher, so ``ORDER BY user_id`` / ``ORDER BY
        label_id`` — the natural way to make a batch deterministic — produce a different
        answer rather than the same one. The two relation kinds are written with
        ``subtask`` first so alphabetical key order and insertion order disagree too.
        """
        from calton.models import Label, LabelTask

        with sessions() as session:
            session.add_all(
                [
                    Label(id=940, title="L-lower-id", created_by_id=ALICE),
                    Label(id=941, title="L-higher-id", created_by_id=BOB),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TaskAssignee(task_id=920, user_id=BOB),
                    TaskAssignee(task_id=920, user_id=ALICE),
                    LabelTask(task_id=920, label_id=941),
                    LabelTask(task_id=920, label_id=940),
                    TaskRelation(
                        task_id=920, other_task_id=922, relation_kind="subtask", created_by_id=ALICE
                    ),
                    TaskRelation(
                        task_id=920, other_task_id=923, relation_kind="related", created_by_id=ALICE
                    ),
                    # Unreadable far end, so the comparison also covers the filtered case.
                    TaskRelation(
                        task_id=920, other_task_id=927, relation_kind="related", created_by_id=ALICE
                    ),
                ]
            )
            session.commit()

    def test_the_collection_entry_equals_the_single_read(
        self, richly_shaped_task: None, client: TestClient
    ) -> None:
        single = client.get("/api/v1/tasks/920")
        assert single.status_code == 200, single.text

        collection = client.get("/api/v1/projects/920/tasks")
        assert collection.status_code == 200, collection.text
        batched = next(entry for entry in collection.json() if entry["id"] == 920)

        assert batched == single.json()

    def test_the_fixture_actually_distinguishes_the_orderings(
        self, richly_shaped_task: None, client: TestClient
    ) -> None:
        """The equality above is vacuous if the seed's orderings are fixed points.

        Insertion order equalling id order would let ``ORDER BY user_id`` pass the
        comparison, so this checks the seed is discriminating before trusting what the
        comparison proves — the two answers have to be different answers.
        """
        body = client.get("/api/v1/tasks/920").json()

        assert [user["id"] for user in body["assignees"]] == [BOB, ALICE]
        assert [label["id"] for label in body["labels"]] == [941, 940]
        assert list(body["related_tasks"]) == ["related", "subtask"]
        assert [entry["id"] for entry in body["related_tasks"]["related"]] == [923]


class TestTheEmbeddedCommentCapIsPerTaskNotPerPage:
    """Two commented tasks on one page, which is what tells the two caps apart.

    ``test_task_expand.py`` already asserts "55 in, 50 out" and its docstring already says
    the cap is per task rather than per request — but its fixture comments a *single*
    task, and with one commented task a page-wide ``LIMIT 50`` returns exactly the same 50
    rows. The claim was therefore true and untested: the seed is a fixed point for the
    difference between the two rules.

    It matters more now than it did, because the page-wide form is the *natural* way to
    write the batched query and the per-task form needs a window function to express.
    """

    @pytest.fixture
    def two_commented_tasks(self, sessions: sessionmaker[Session], expand_seed: None) -> None:
        """A second task with 30 comments, alongside the fixture's parent with 55.

        30 is chosen so the second task is under the cap on its own but pushed past it by
        the first: a page-wide limit of 50 spends all of it on the parent and leaves this
        task empty, while the per-task rule returns all 30.
        """
        from calton.models import TaskComment

        with sessions() as session:
            for n in range(30):
                session.add(
                    TaskComment(
                        id=9500 + n,
                        task_id=9804,
                        comment=f"lonely-{n:03d}",
                        author_id=ALICE,
                        created=datetime(2026, 2, 1, tzinfo=UTC),
                        updated=datetime(2026, 2, 1, tzinfo=UTC),
                    )
                )
            session.commit()

    def test_both_tasks_keep_their_own_allowance(
        self, two_commented_tasks: None, client: TestClient
    ) -> None:
        response = client.get("/api/v1/projects/980/tasks", params={"expand": "comments"})
        assert response.status_code == 200, response.text
        body = {entry["id"]: entry for entry in response.json()}

        assert len(body[9800]["comments"]) == 50
        assert body[9800]["comments"][0]["comment"] == "comment-000"
        assert body[9800]["comments"][-1]["comment"] == "comment-049"

        # The one a page-wide cap would have starved.
        assert len(body[9804]["comments"]) == 30
        assert body[9804]["comments"][0]["comment"] == "lonely-000"
        assert body[9804]["comments"][-1]["comment"] == "lonely-029"


class TestThePrefetchGuardsRefuseMisuse:
    """Both prefetch objects raise rather than answering from the wrong data.

    Neither guard has a caller that violates it today, so removing them changes no
    response — which is exactly why they need tests of their own. A guard nothing
    exercises is indistinguishable from a comment, and both of these stand in front of a
    failure that answers **200 with quietly wrong data**: a task serialised against a
    prefetch that does not cover it loses its assignees, labels and relations, and
    placements from another view fill in a plausible ``bucket_id`` from the wrong board.
    """

    def test_read_view_refuses_a_task_the_prefetch_does_not_cover(self, session: Session) -> None:
        from calton.services import task_service

        covered = session.get(Task, 920)
        uncovered = session.get(Task, 922)
        assert covered is not None and uncovered is not None

        prefetch = task_service.build_prefetch(session, [covered], ALICE)

        with pytest.raises(ValueError, match="not covered by this prefetch"):
            task_service.read_view(session, uncovered, ALICE, prefetch)

    def test_with_placements_refuses_a_mapping_built_for_another_view(
        self, collection_seed: None, session: Session
    ) -> None:
        from calton.services import task_hydration, task_service

        task = session.get(Task, 9700)
        assert task is not None
        view = task_service.read_view(session, task, ALICE)

        placements = task_hydration.build_placements(session, [9700], 973)

        with pytest.raises(ValueError, match="built for view 973, not 970"):
            task_hydration.with_placements(session, [view], [task], 970, placements=placements)
