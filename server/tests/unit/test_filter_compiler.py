"""Filter compilation, executed against a real database rather than inspected as SQL.

The acceptance table is tester's ``_filter_exists.yaml`` corpus (13 ``@critical`` cases)
plus six shapes it does not cover. Every expectation below was **re-measured** against the
Go reference server at 078f21c: the overlay was seeded through the testing endpoints and
each filter run through ``GET /api/v1/tasks``, recording the exact set of task titles.
All 13 agreed with tester's L2 values, including their correction to
``reminders_single_bound``.

The cases assert *which* tasks come back, never how many, because the failure mode here
is a plausible-looking result set. The fixture data exists to separate one implementation
from its opposite:

* ``B-straddle`` has two reminders, 1 May and 1 August, and none between. A merged EXISTS
  (correct) misses it; two independent ones (wrong) match it, because ``> 1 June`` is
  satisfied by the August row and ``< 1 July`` by the May row. A task with one reminder
  cannot tell the two implementations apart.
* ``A-no-labels`` has no labels at all, so it catches ``labels != 910`` compiled as a
  join with an inequality, which drops label-less tasks entirely.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.core.errors import CaltonError
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.db.types import CaltonDateTime
from calton.filters.compiler import compile_filter_string
from calton.models.label import Label, LabelTask
from calton.models.task import Task
from calton.models.task_assignee import TaskAssignee
from calton.models.task_reminder import TaskReminder
from calton.models.user import User

CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def _reminder(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    """The overlay from ``seed/overlay/filter_exists.yml``, as rows."""
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)

    with session_factory(engine)() as db:
        db.add_all(
            [
                User(id=900, username="alice", created=CREATED, updated=CREATED),
                User(id=901, username="bob", created=CREATED, updated=CREATED),
            ]
        )
        for label_id, title in ((910, "L-alpha"), (911, "L-beta"), (912, "L-gamma")):
            db.add(
                Label(id=label_id, title=title, created_by_id=900, created=CREATED, updated=CREATED)
            )

        titles = {
            910: "A-both",
            911: "A-only-alpha",
            912: "A-only-beta",
            913: "A-no-labels",
            914: "B-inside",
            915: "B-straddle",
            916: "B-outside",
            917: "G-both-assignees",
            918: "G-only-alice",
        }
        for position, (task_id, title) in enumerate(titles.items(), start=1):
            db.add(
                Task(
                    id=task_id,
                    project_id=910,
                    index=position,
                    title=title,
                    created_by_id=900,
                    created=CREATED,
                    updated=CREATED,
                )
            )

        for row_id, (task_id, label_id) in enumerate(
            [(910, 910), (910, 911), (911, 910), (912, 911), (915, 912)], start=1
        ):
            db.add(LabelTask(id=row_id, task_id=task_id, label_id=label_id, created=CREATED))

        # B-straddle's two reminders sit either side of the June window with nothing
        # inside it; that is the whole point of the fixture.
        for row_id, (task_id, moment) in enumerate(
            [
                (914, "2026-06-15T00:00:00"),
                (915, "2026-05-01T00:00:00"),
                (915, "2026-08-01T00:00:00"),
                (916, "2026-04-01T00:00:00"),
            ],
            start=1,
        ):
            db.add(
                TaskReminder(
                    id=row_id, task_id=task_id, reminder=_reminder(moment), created=CREATED
                )
            )

        for row_id, (task_id, user_id) in enumerate([(917, 900), (917, 901), (918, 900)], start=1):
            db.add(TaskAssignee(id=row_id, task_id=task_id, user_id=user_id, created=CREATED))

        db.commit()
        _store_timestamps_the_way_the_fixtures_do(db)
        yield db


def _store_timestamps_the_way_the_fixtures_do(db: Session) -> None:
    """Rewrite every seeded timestamp to the spelling the real database holds.

    ⚠️ Without this the tests below are measured against data no server ever produces.
    These columns are TEXT and are compared as TEXT, so the *spelling* decides what an
    equality filter matches. Every timestamp in the seed — and everything upstream's
    fixture loader writes — is ``2026-05-01 00:00:00+00:00``, while writing the same
    instant through the ORM, as the rows above do, produces ``2026-05-01 00:00:00``.

    The two never compare equal, so a compiler that matched the *ORM* spelling passed
    this file and returned an empty set against the reference server, and a compiler
    that matches the real spelling does the reverse. That is not a detail of the test
    setup: it decides which of the two this file rewards. It rewarded the wrong one, and
    ``TestAgainstTheCorpus`` is named for the claim that it does not.
    """
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, CaltonDateTime):
                continue
            db.execute(
                sql_text(
                    f"update {table.name} set {column.name} = {column.name} || '+00:00' "
                    f"where {column.name} is not null and {column.name} not like '%+00:00'"
                )
            )
    db.commit()


def matching_titles(
    session: Session, filter_string: str, *, include_nulls: bool = False
) -> list[str]:
    condition = compile_filter_string(filter_string, include_nulls=include_nulls)
    query = select(Task.title)
    if condition is not None:
        query = query.where(condition)
    return sorted(session.scalars(query))


ALL_TITLES = sorted(
    [
        "A-both",
        "A-no-labels",
        "A-only-alpha",
        "A-only-beta",
        "B-inside",
        "B-outside",
        "B-straddle",
        "G-both-assignees",
        "G-only-alice",
    ]
)

#: id, filter, expected titles — every row measured against the Go server.
CORPUS = [
    ("labels_and_two_values", "labels = 910 && labels = 911", ["A-both"]),
    (
        "labels_or_two_values",
        "labels = 910 || labels = 911",
        ["A-both", "A-only-alpha", "A-only-beta"],
    ),
    ("labels_in_list", "labels in 910,911", ["A-both", "A-only-alpha", "A-only-beta"]),
    (
        "reminders_range_merged",
        "reminders > '2026-06-01' && reminders < '2026-07-01'",
        ["B-inside"],
    ),
    ("reminders_single_bound", "reminders > '2026-06-01'", ["B-inside", "B-straddle"]),
    (
        "reminders_range_broken_by_other_table",
        "reminders > '2026-06-01' && labels = 912 && reminders < '2026-07-01'",
        ["B-straddle"],
    ),
    (
        "reminders_range_broken_by_or",
        "reminders > '2026-06-01' || reminders < '2026-07-01'",
        ["B-inside", "B-outside", "B-straddle"],
    ),
    (
        "equality_then_range_not_merged",
        "reminders = '2026-05-01' && reminders > '2026-06-01'",
        ["B-straddle"],
    ),
    (
        "labels_not_equals_is_not_exists",
        "labels != 910",
        [
            "A-no-labels",
            "A-only-beta",
            "B-inside",
            "B-outside",
            "B-straddle",
            "G-both-assignees",
            "G-only-alice",
        ],
    ),
    (
        "labels_not_in_is_not_exists",
        "labels not in 910,911",
        ["A-no-labels", "B-inside", "B-outside", "B-straddle", "G-both-assignees", "G-only-alice"],
    ),
    ("assignees_and_two_values", "assignees = alice && assignees = bob", ["G-both-assignees"]),
    ("assignees_like_is_silently_dropped", "assignees like 'ali'", ALL_TITLES),
]

#: Shapes the corpus does not cover, measured the same way.
EXTRA = [
    (
        "three_range_all_merged",
        "reminders > '2026-04-15' && reminders < '2026-07-01' && reminders > '2026-06-01'",
        ["B-inside"],
    ),
    (
        # Deliberately NOT named after group-breaking: 2026-08-01 satisfies
        # "> 2026-06-01" by itself, so the merged and unmerged readings are logically
        # identical here whatever the seed data holds. Kept as a plain regression case;
        # range_then_equality_below_bound is the one that discriminates.
        "range_then_equality_above_bound_is_not_discriminating",
        "reminders > '2026-06-01' && reminders = '2026-08-01'",
        ["B-straddle"],
    ),
    (
        # The discriminating form: the equality value sits BELOW the bound, so no single
        # reminder can satisfy both. Correct (two EXISTS) -> B-straddle; a merging
        # implementation -> nothing at all.
        "range_then_equality_below_bound",
        "reminders > '2026-06-01' && reminders = '2026-05-01'",
        ["B-straddle"],
    ),
    (
        # Condition 3 on its own: two range comparators, adjacent and AND-joined, but on
        # different sub-tables. Merging them would apply both to the first field's
        # column -- an integer compared against a datetime.
        "range_across_two_subtables",
        "reminders > '2026-06-01' && labels > 910",
        ["B-straddle"],
    ),
    (
        "labels_gte_lte_merged",
        "labels >= 910 && labels <= 911",
        ["A-both", "A-only-alpha", "A-only-beta"],
    ),
    ("mixed_tables_adjacent", "labels = 910 && assignees = alice", []),
    (
        "not_equals_then_range",
        "reminders != '2026-05-01' && reminders > '2026-06-01'",
        ["B-inside"],
    ),
]


class TestAgainstTheCorpus:
    @pytest.mark.parametrize(
        ("filter_string", "expected"),
        [(f, e) for _, f, e in CORPUS],
        ids=[case_id for case_id, _, _ in CORPUS],
    )
    def test_matches_the_go_server(
        self, session: Session, filter_string: str, expected: list[str]
    ) -> None:
        assert matching_titles(session, filter_string) == sorted(expected)

    @pytest.mark.parametrize(
        ("filter_string", "expected"),
        [(f, e) for _, f, e in EXTRA],
        ids=[case_id for case_id, _, _ in EXTRA],
    )
    def test_matches_the_go_server_on_extra_shapes(
        self, session: Session, filter_string: str, expected: list[str]
    ) -> None:
        assert matching_titles(session, filter_string) == sorted(expected)

    def test_the_baseline_is_every_task(self, session: Session) -> None:
        assert matching_titles(session, "") == ALL_TITLES


class TestMergeIsPositional:
    """The four merge conditions, each shown failing to merge on its own."""

    def test_two_bounds_merge_into_one_exists(self, session: Session) -> None:
        """B-straddle is excluded: no single reminder is inside the window."""
        assert matching_titles(session, "reminders > '2026-06-01' && reminders < '2026-07-01'") == [
            "B-inside"
        ]

    def test_an_intervening_other_table_breaks_the_group(self, session: Session) -> None:
        """The adjacency condition, and the one a field-grouping implementation fails.

        Grouping by field name would merge the two reminder bounds despite the labels
        condition between them, and return nothing at all instead of B-straddle.
        """
        merged_by_field_would_return: list[str] = []
        actual = matching_titles(
            session,
            "reminders > '2026-06-01' && labels = 912 && reminders < '2026-07-01'",
        )

        assert actual == ["B-straddle"]
        assert actual != merged_by_field_would_return

    def test_or_breaks_the_group(self, session: Session) -> None:
        assert matching_titles(session, "reminders > '2026-06-01' || reminders < '2026-07-01'") == [
            "B-inside",
            "B-outside",
            "B-straddle",
        ]

    def test_a_leading_equality_never_starts_a_group(self, session: Session) -> None:
        """The scan only begins a group on a range comparator."""
        assert matching_titles(session, "reminders = '2026-05-01' && reminders > '2026-06-01'") == [
            "B-straddle"
        ]

    def test_a_trailing_equality_ends_the_group(self, session: Session) -> None:
        """The equality value must sit *below* the bound, or the case cannot fail.

        Above the bound (2026-08-01) a single reminder satisfies both conditions, so
        merging and not merging agree no matter what the seed holds — the assertion
        passes against either implementation and proves nothing. Below it they disagree:
        correct gives B-straddle, a merging one gives nothing at all.
        """
        assert matching_titles(session, "reminders > '2026-06-01' && reminders = '2026-05-01'") == [
            "B-straddle"
        ]

    def test_a_different_sub_table_ends_the_group(self, session: Session) -> None:
        """Condition 3, which nothing else here covers.

        Both sides are range comparators, adjacent and AND-joined, so conditions 1, 2 and
        4 all hold and only the table differs. The existing mixed-table case uses ``=``,
        which condition 1 rejects first, so it never reaches this check.
        """
        assert matching_titles(session, "reminders > '2026-06-01' && labels > 910") == [
            "B-straddle"
        ]

    def test_equality_pairs_are_two_independent_exists(self, session: Session) -> None:
        """ "Has both labels", which a merged EXISTS would make impossible."""
        assert matching_titles(session, "labels = 910 && labels = 911") == ["A-both"]
        # The OR control proves the empty case above would be a real difference.
        assert matching_titles(session, "labels = 910 || labels = 911") == [
            "A-both",
            "A-only-alpha",
            "A-only-beta",
        ]


class TestNotExists:
    def test_a_task_with_no_rows_at_all_matches_not_equals(self, session: Session) -> None:
        """The three-valued-logic trap: a join with ``!=`` would drop A-no-labels."""
        assert "A-no-labels" in matching_titles(session, "labels != 910")

    def test_not_equals_still_excludes_the_named_label(self, session: Session) -> None:
        titles = matching_titles(session, "labels != 910")
        assert "A-both" not in titles
        assert "A-only-alpha" not in titles

    def test_not_in_behaves_the_same_way(self, session: Session) -> None:
        titles = matching_titles(session, "labels not in 910,911")
        assert "A-no-labels" in titles
        assert not {"A-both", "A-only-alpha", "A-only-beta"} & set(titles)


class TestAssignees:
    def test_filtering_is_by_username_not_id(self, session: Session) -> None:
        assert matching_titles(session, "assignees = alice") == [
            "G-both-assignees",
            "G-only-alice",
        ]
        # A user id would find nothing, since the column compared is users.username.
        assert matching_titles(session, "assignees = 900") == []

    def test_like_is_dropped_rather_than_applied_or_rejected(self, session: Session) -> None:
        """Both wrong implementations are caught: raising 4019, and actually matching.

        Comparing against the unfiltered set is what rules out a real LIKE, which would
        return only the two alice tasks.
        """
        assert matching_titles(session, "assignees like 'ali'") == ALL_TITLES

    def test_like_is_dropped_even_alongside_other_conditions(self, session: Session) -> None:
        """Only the assignees condition disappears; the rest of the filter still applies."""
        assert matching_titles(session, "assignees like 'ali' && labels = 910") == [
            "A-both",
            "A-only-alpha",
        ]

    def test_like_on_another_sub_table_is_not_dropped(self, session: Session) -> None:
        """The rule is specific to assignees, not to sub-tables in general.

        ``labels`` casts its value to an integer, and ``like`` on a non-string is 4019 —
        measured, after this test first asserted an empty result and was wrong.
        """
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string("labels like 910")

        assert excinfo.value.code == 4019


class TestIncludeNulls:
    def test_tasks_with_no_sub_table_rows_are_included(self, session: Session) -> None:
        """B-outside has a reminder that fails the test, so it stays excluded."""
        titles = matching_titles(session, "reminders > '2026-06-01'", include_nulls=True)

        assert titles == sorted(set(ALL_TITLES) - {"B-outside"})

    def test_a_plain_column_also_matches_null(self, session: Session) -> None:
        titles = matching_titles(session, "due_date > '2026-01-01'", include_nulls=True)
        assert titles == ALL_TITLES

    def test_numeric_columns_also_match_zero(self, session: Session) -> None:
        """An int column holds 0 when nothing was written, so 0 counts as unset too."""
        session.execute(update(Task).where(Task.id == 910).values(priority=0))
        session.execute(update(Task).where(Task.id == 911).values(priority=5))
        session.commit()

        titles = matching_titles(session, "priority > 3", include_nulls=True)

        assert "A-both" in titles  # priority 0
        assert "A-only-alpha" in titles  # priority 5, matches outright
        assert "B-inside" in titles  # priority NULL


class TestLeafConditions:
    def test_like_wraps_the_value_in_percent_signs_on_both_sides(self, session: Session) -> None:
        """A prefix-only LIKE would miss these, since the match is mid-string."""
        assert matching_titles(session, "title like inside") == ["B-inside"]
        assert matching_titles(session, "title like both") == [
            "A-both",
            "G-both-assignees",
        ]

    def test_boolean_values_are_parsed(self, session: Session) -> None:
        session.execute(update(Task).where(Task.id == 910).values(done=True))
        session.commit()

        assert matching_titles(session, "done = true") == ["A-both"]

    def test_in_splits_on_commas(self, session: Session) -> None:
        assert matching_titles(session, "title in A-both,B-inside") == [
            "A-both",
            "B-inside",
        ]

    def test_joins_fold_left_without_precedence(self, session: Session) -> None:
        """``a || b && c`` groups as ``(a || b) && c``, matching upstream's fold.

        With AND-over-OR precedence it would be ``a || (b && c)`` and A-both would match.
        """
        titles = matching_titles(session, "title = A-both || title = B-inside && title = B-inside")

        assert titles == ["B-inside"]


class TestTheTwoFieldsThatAreNotTaskColumns:
    """``bucket_id`` and ``position`` are structurally identical and handled differently.

    Both live on association tables rather than on ``tasks``. T28 closed the first and
    deliberately left the second open; these two assertions are what keep the asymmetry
    from being "tidied up" in either direction.
    """

    def test_bucket_id_compiles_to_a_subquery(self) -> None:
        """Closed by T28. It used to raise 4016 while upstream answered 200.

        No view context is needed even though a task's bucket is per-view, because a
        bucket belongs to exactly one view — so the bucket id already pins it. Measured
        on the reference server: the same filter returns the same three tasks through a
        Kanban view, through a List view, and through two endpoints with no view at all.

        The earlier reading of upstream — "200 with an empty list, so the field does
        nothing" — came from probing with bucket 3, which has no tasks. That input cannot
        distinguish a working filter from an ignored one.
        """
        compiled = compile_filter_string("bucket_id = 970")
        assert compiled is not None
        assert "task_buckets" in str(compiled)

    def test_bucket_id_negation_excludes_instead_of_including(self) -> None:
        """``!=`` has to become NOT IN, not a negated membership of the wrong set.

        Asserted separately because the positive case passes either way: a subquery built
        for ``=`` and reused for ``!=`` still mentions ``task_buckets`` and still compiles.
        """
        assert "NOT IN" in str(compile_filter_string("bucket_id != 970")).upper()

    def test_position_is_rejected_here_and_500s_upstream(self) -> None:
        """The same shape as bucket_id, except upstream *crashes*: measured 500.

        ``position`` lives on ``task_positions`` and needs the same view context. Ours is
        a controlled 400 rather than a 500, so this one must not be "fixed" by copying
        upstream — reproducing a crash would be worse than diverging from it.
        """
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string("position > 1")

        assert excinfo.value.code == 4016


class TestInvalidValues:
    def test_an_unparseable_date_is_4019(self, session: Session) -> None:
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string("due_date > 'not a date'")

        assert excinfo.value.code == 4019

    def test_an_unparseable_number_is_4019(self, session: Session) -> None:
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string("priority > abc")

        assert excinfo.value.code == 4019

    def test_an_unparseable_boolean_is_4019(self, session: Session) -> None:
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string("done = maybe")

        assert excinfo.value.code == 4019

    @pytest.mark.parametrize(
        "filter_string",
        ["labels like 910", "labels like abc", "priority like 3", "reminders like x"],
    )
    def test_like_on_a_non_string_field_is_4019(self, filter_string: str) -> None:
        """Measured against the Go server, which answers 400/4019 for each of these.

        Known cosmetic divergence: the Go message interpolates the value with the wrong
        format verb (``'%!s(int64=910)'``) and, for sub-table fields, names the inner
        column (``label_id``) rather than the filter field. The code and status match;
        the message text does not, and reproducing a formatting bug byte-for-byte is not
        worth it here. Flagged for the parity harness.
        """
        with pytest.raises(CaltonError) as excinfo:
            compile_filter_string(filter_string)

        assert excinfo.value.code == 4019
        assert excinfo.value.http_status == 400

    def test_like_on_a_string_field_still_works(self, session: Session) -> None:
        assert matching_titles(session, "title like both") == [
            "A-both",
            "G-both-assignees",
        ]

    def test_date_maths_is_accepted_in_a_value(self, session: Session) -> None:
        assert compile_filter_string("due_date > now/d") is not None
