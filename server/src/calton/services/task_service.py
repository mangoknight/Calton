"""Task business rules: create, read, update, delete, and read-by-index.

Every behaviour below was measured against the Go reference server rather than read off
``pkg/models/tasks.go``. Three of those measurements contradict the project's own design
documents, and each is enforced by a test that names it:

1. **``title`` and ``project_id`` are AC-6 exceptions.** The design doc (§2.3.1) and the
   T18 card both state Task has *no* exceptions, reasoning that ``Task.Update`` freezes a
   14-column ``colsToUpdate`` with no conditional appends. That is true and still gives
   the wrong answer, because the column list is not what decides the value: ``Update``
   ends with ``mergo.Merge(&ot, t, mergo.WithOverride)``, and **mergo skips zero values in
   the source**. A field is therefore only reset to zero if it appears in the explicit
   "mergo does ignore nil values" block at ``tasks.go:1543-1589`` — and ``title`` and
   ``project_id`` are not in it (``project_id`` is additionally guarded at ``:1264``).
   Measured: POST ``{"id": N, "title": ""}`` against a task titled ``T-full`` answers 200
   and leaves the title ``T-full``. The corpus records the same
   (``task.update.empty_title_is_ignored``).

2. **The update response is the request merged over the stored row, not a re-read.**
   Read-only fields sent by the client come straight back while the database keeps its
   own value. Measured: ``index: 99`` in, ``index: 99`` out, ``index: 1`` on the next GET.
   The obvious implementation — update, re-select, serialise — returns ``index: 1`` and
   fails parity.

3. **Empty collections differ between create and read** and must not be unified; see the
   module docstring of ``schemas.task``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.db.base import utcnow
from calton.db.types import ZERO_TIME, as_utc, format_rfc3339, parse_rfc3339
from calton.models import Favorite, Label, LabelTask, Project, User
from calton.models.file import File
from calton.models.task import Task, base_task_query
from calton.models.task_assignee import TaskAssignee
from calton.models.task_comment import TaskAttachment
from calton.models.task_relation import TaskRelation
from calton.models.task_reminder import TaskReminder
from calton.permissions.project import NO_PERMISSION, max_permissions_for_projects
from calton.schemas.label import LabelRead
from calton.schemas.task import (
    MAX_TASK_REPEAT_AFTER_SECONDS,
    TaskRead,
    TaskWrite,
    TaskWriteResponse,
)
from calton.schemas.user import UserRead
from calton.services import assignee_service

#: ``models.FavoriteKindTask`` (favorites.go). Favourites are keyed by (entity, user, kind).
FAVORITE_KIND_TASK = 1

#: ``TaskRepeatMode*`` (tasks.go). Stored as an int; unlike view_kind it stays an int on
#: the wire too, so there is no enum translation here.
REPEAT_MODE_DEFAULT = 0
REPEAT_MODE_MONTH = 1
REPEAT_MODE_FROM_CURRENT_DATE = 2

#: Columns ``Task.Update`` writes where an omitted (zero) value really does store zero.
#: This is ``colsToUpdate`` (``tasks.go:1283``) minus the three that survive it:
#: ``title`` and ``project_id`` (exception 1 above) and ``bucket_id``, which is
#: ``xorm:"-"`` — naming it in ``Cols()`` updates nothing because there is no such column.
#: Bucket membership lives in ``task_buckets`` and belongs to T28.
FULLY_REPLACED_COLUMNS = (
    "description",
    "done",
    "due_date",
    "repeat_after",
    "priority",
    "start_date",
    "end_date",
    "hex_color",
    "percent_done",
    "repeat_mode",
    "cover_image_attachment_id",
)

#: The other half of the same rule: every ``tasks`` column that an omitted value does
#: **not** reset, and why.
#:
#: ⚠️ This exists so the pair can be reconciled against ``Task.__table__.columns``.
#: ``FULLY_REPLACED_COLUMNS`` alone is an enumeration nobody is reminded to update, and
#: its test used to compare it against a table in the test file — two hand-maintained
#: lists agreeing with each other and with nothing external, so adding a column to the
#: model touched neither and the suite stayed green while the new column silently
#: escaped full replacement. Together the two lists must account for **every** column,
#: which is a fact about the model rather than about either list, so a new column now
#: fails until someone has decided which half it belongs to.
#:
#: The values are the reason, not decoration: "why does this column survive an omitted
#: value" is exactly the question the next person has to answer, and three of these have
#: non-obvious answers that were measured rather than reasoned.
NOT_REPLACED_COLUMNS: dict[str, str] = {
    "title": (
        "AC-6 exception. In colsToUpdate, but mergo skips zero values in the source and "
        "title is absent from the explicit nil-values block (tasks.go:1543-1589). "
        "Measured: POST {'id': N, 'title': ''} leaves the stored title untouched."
    ),
    "project_id": (
        "AC-6 exception, same mergo reasoning as title, and additionally guarded at tasks.go:1264."
    ),
    "id": (
        "Primary key. It identifies the row being updated rather than being one of the "
        "things the update writes, and the request body carries it for that reason."
    ),
    "index": (
        "Allocated at create and never rewritten. The update *echoes* whatever the "
        "client sent — measured: index 99 in, 99 out, 1 on the next GET — which is the "
        "response-shape rule in exception 2 above, not a column write."
    ),
    "uid": (
        "Allocated at create and never named in colsToUpdate, so an omitted uid cannot "
        "blank it. Distinct from `index`: uid is global, index is per project."
    ),
    "created": (
        "Set at create and never rewritten. The update response *echoes* a client-sent "
        "value without persisting it, which is the same read-only echo as `index`."
    ),
    "updated": (
        "Written by the update itself from the server clock, so it is never taken from "
        "the request and an omitted value is meaningless here."
    ),
    "done_at": (
        "Derived from the `done` transition by the service, not taken from the request. "
        "Not in colsToUpdate."
    ),
    "deleted_at": (
        "Soft-delete bookkeeping, written only by the delete path. An update that could "
        "reset it would resurrect a deleted task, and base_task_query hides those rows."
    ),
    "created_by_id": (
        "Set at create from the authenticated subject. A client cannot reassign "
        "authorship, so this is a permission boundary and not only a column choice."
    ),
}

#: How many times index allocation retries a unique-constraint collision before giving up.
#: Each retry re-reads the high-water mark, so concurrent creators converge; the bound
#: only stops a pathological live-lock from hanging the request.
INDEX_ALLOCATION_ATTEMPTS = 10


def _task_not_found() -> CaltonError:
    """404/4002. Note the message has no full stop — unlike the project one. Measured."""
    return CaltonError.from_name("models.ErrTaskDoesNotExist")


def _project_not_found() -> CaltonError:
    return CaltonError.from_name("models.ErrProjectDoesNotExist")


def _validate_repeat_after(repeat_after: int) -> None:
    """``validateRepeatAfter`` (tasks.go:57). 400/4029 with the bound in the message."""
    if repeat_after < 0 or repeat_after > MAX_TASK_REPEAT_AFTER_SECONDS:
        raise CaltonError.from_name(
            "models.ErrInvalidTaskRepeatInterval",
            max_task_repeat_after_seconds=MAX_TASK_REPEAT_AFTER_SECONDS,
        )


def is_repeating(repeat_after: int, repeat_mode: int) -> bool:
    """``Task.isRepeating`` (tasks.go:214).

    Public because the bucket line needs the identical rule: dragging a repeating task
    into the done bucket reschedules it instead of completing it. A second copy of this
    would have to be kept in step with ``repeat_mode`` by hand, and only one of the two
    call sites has a corpus case for each mode — so the copy that drifted would be the
    one nothing noticed.
    """
    return repeat_after > 0 or repeat_mode == REPEAT_MODE_MONTH


def _add_repeat_interval(now: datetime, value: datetime, interval: timedelta) -> datetime:
    """``addRepeatIntervalToTime`` (tasks.go:1729).

    Advances ``value`` by whole intervals until it is in the future, and by at least one
    interval even when it already is. Landing *on* ``now`` counts as not-before, so the
    result is always strictly later than the input.
    """
    if interval <= timedelta(0):
        return value
    if value >= now:
        return value + interval
    intervals = (now - value) // interval + 1
    return value + intervals * interval


def _add_one_month(value: datetime) -> datetime:
    """``addOneMonthToDate`` — calendar month, so the day-of-month is kept where it exists."""
    year, month = divmod(value.month, 12)
    month += 1
    year += value.year
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


class RepeatDates:
    """The three reschedulable dates, so the repeat helpers can return them as a unit."""

    __slots__ = ("due", "end", "start")

    def __init__(self, due: datetime, start: datetime, end: datetime) -> None:
        self.due, self.start, self.end = due, start, end


def reschedule(old: Task, dates: RepeatDates, now: datetime) -> RepeatDates:
    """The next occurrence's dates, per ``repeat_mode`` (``setTaskDates*Repeat``).

    Reads ``old`` for every input, as upstream does: "everything in oldTask is the truth".
    Measured for the default mode: a task due 2026-03-01T12:00 with a one-day interval,
    completed today, moves to *tomorrow* 12:00 — the interval is counted from now, not
    from the stored due date, and the time of day survives.
    """
    interval = timedelta(seconds=old.repeat_after or 0)

    if old.repeat_mode == REPEAT_MODE_MONTH:
        due = _add_one_month(dates.due) if dates.due != ZERO_TIME else dates.due
        if dates.start != ZERO_TIME and dates.end != ZERO_TIME:
            gap = dates.end - dates.start
            start = _add_one_month(dates.start)
            return RepeatDates(due, start, start + gap)
        start = _add_one_month(dates.start) if dates.start != ZERO_TIME else dates.start
        end = _add_one_month(dates.end) if dates.end != ZERO_TIME else dates.end
        return RepeatDates(due, start, end)

    if old.repeat_after == 0:
        return dates

    if old.repeat_mode == REPEAT_MODE_FROM_CURRENT_DATE:
        due = now + interval if dates.due != ZERO_TIME else dates.due
        if dates.due == ZERO_TIME:
            if dates.start != ZERO_TIME and dates.end != ZERO_TIME:
                gap = dates.end - dates.start
                return RepeatDates(due, now + interval, now + interval + gap)
            start = now + interval if dates.start != ZERO_TIME else dates.start
            end = now + interval if dates.end != ZERO_TIME else dates.end
            return RepeatDates(due, start, end)
        start = due - (dates.due - dates.start) if dates.start != ZERO_TIME else dates.start
        end = due - (dates.due - dates.end) if dates.end != ZERO_TIME else dates.end
        return RepeatDates(due, start, end)

    due = _add_repeat_interval(now, dates.due, interval) if dates.due != ZERO_TIME else dates.due
    start = (
        _add_repeat_interval(now, dates.start, interval)
        if dates.start != ZERO_TIME
        else dates.start
    )
    end = _add_repeat_interval(now, dates.end, interval) if dates.end != ZERO_TIME else dates.end
    return RepeatDates(due, start, end)


def _next_index(session: Session, project_id: int) -> int:
    """``calculateNextTaskIndex`` (tasks.go:875): ``max(index) + 1``, counting deleted rows.

    Deliberately **not** ``base_task_query``: the scan is ``Unscoped`` upstream so a
    soft-deleted task keeps its index reserved. Reusing it would resurrect the deleted
    task's identifier on a new task, and ``by-index`` would then be ambiguous. Verified:
    after deleting the task holding index 52, the next create got 53.
    """
    highest = session.scalar(select(func.max(Task.index)).where(Task.project_id == project_id))
    return (highest or 0) + 1


def _identifier(project: Project | None, index: int) -> str:
    """``Task.setIdentifier`` (tasks.go:484). A project without an identifier gives ``#N``."""
    if project is None or not project.identifier:
        return f"#{index}"
    return f"{project.identifier}-{index}"


def _project(
    session: Session, project_id: int, prefetch: ReadPrefetch | None = None
) -> Project | None:
    if prefetch is not None:
        return prefetch.projects.get(project_id)
    return session.scalars(select(Project).where(Project.id == project_id)).one_or_none()


def user_view(
    session: Session, user_id: int | None, prefetch: ReadPrefetch | None = None
) -> UserRead | None:
    """The nested user object, or ``None`` for a null id or a user that no longer exists.

    The prefetch memoises rather than enumerating (see :class:`ReadPrefetch`): an id it
    has not seen is looked up once and cached, so callers holding ids the builder could
    not know about — a bucket's creator, for one — stay correct at one query per distinct
    user instead of one per row.
    """
    if user_id is None:
        return None
    if prefetch is not None and user_id in prefetch.users:
        user = prefetch.users[user_id]
    else:
        user = session.scalars(select(User).where(User.id == user_id)).one_or_none()
        if prefetch is not None:
            prefetch.users[user_id] = user
    if user is None:
        return None
    return UserRead.model_validate(user, from_attributes=True)


def _is_favorite(
    session: Session, task_id: int, user_id: int, prefetch: ReadPrefetch | None = None
) -> bool:
    if prefetch is not None:
        return task_id in prefetch.favorite_task_ids
    row = session.scalars(
        select(Favorite).where(
            Favorite.entity_id == task_id,
            Favorite.user_id == user_id,
            Favorite.kind == FAVORITE_KIND_TASK,
        )
    ).one_or_none()
    return row is not None


def set_favorite(session: Session, task_id: int, user_id: int, favorite: bool) -> None:
    existing = session.scalars(
        select(Favorite).where(
            Favorite.entity_id == task_id,
            Favorite.user_id == user_id,
            Favorite.kind == FAVORITE_KIND_TASK,
        )
    ).one_or_none()
    if favorite and existing is None:
        session.add(Favorite(entity_id=task_id, user_id=user_id, kind=FAVORITE_KIND_TASK))
    elif not favorite and existing is not None:
        session.delete(existing)


def _columns(task: Task) -> TaskRead:
    """The task's own persisted fields, with every computed field still at its default.

    The ``or`` fallbacks are not cosmetic: several of these columns are nullable in the
    schema (Go writes zero values, but seeds and migrations leave NULLs), while the Go
    struct fields are non-pointer and have no null on the wire. NULL therefore has to
    become the zero value here or the response carries a ``null`` upstream never sends.
    """
    return TaskRead(
        id=task.id,
        title=task.title,
        description=task.description or "",
        done=bool(task.done),
        done_at=task.done_at,
        due_date=task.due_date,
        project_id=task.project_id,
        repeat_after=task.repeat_after or 0,
        repeat_mode=task.repeat_mode,
        priority=task.priority or 0,
        start_date=task.start_date,
        end_date=task.end_date,
        hex_color=task.hex_color or "",
        percent_done=task.percent_done or 0,
        index=task.index,
        cover_image_attachment_id=task.cover_image_attachment_id or 0,
        created=task.created,
        updated=task.updated,
    )


def _nested_related_task(
    session: Session, task: Task, user_id: int, prefetch: ReadPrefetch | None = None
) -> dict[str, Any]:
    """A task as it appears **inside** another task's ``related_tasks``.

    Columns plus ``is_favorite``, and nothing else: ``identifier`` stays the empty string
    even when the project has one, ``created_by`` stays null, and ``related_tasks`` is
    null rather than ``{}``. That is ``addRelatedTasksToTasks`` (``tasks.go:585``)
    declining to recurse — the comment there says so, and it sets ``IsFavorite`` by hand
    right before copying, which is why that one field *is* filled in.

    Rendering these with the ordinary read view is the natural implementation and changes
    three fields at once (``identifier`` becomes "AS-3", ``created_by`` a user object,
    ``related_tasks`` a map). Nothing breaks — it is a pure wire difference, so only the
    parity harness can see it — and recursing also risks N+1 queries and, where two tasks
    relate to each other, unbounded recursion.
    """
    return (
        _columns(task)
        .model_copy(update={"is_favorite": _is_favorite(session, task.id, user_id, prefetch)})
        .model_dump(mode="json")
    )


def _related_tasks(
    session: Session, task_id: int, user_id: int, prefetch: ReadPrefetch | None = None
) -> dict[str, Any]:
    """``related_tasks`` for one task: relations grouped by kind, permission-filtered.

    Three measured properties, each of which rules out an obvious implementation:

    * **Keys come out in alphabetical order**, because Go's ``encoding/json`` sorts map
      keys. Python dicts keep insertion order, so a task whose ``subtask`` row was written
      first would serialise ``{"subtask": …, "related": …}`` — same data, different bytes,
      and the parity harness compares bytes.
    * **Within a kind, the order is the relation row id**, not the other task's id.
      Measured with three rows whose ids run opposite to their ``other_task_id``: the
      answer follows the row ids.
    * **A relation whose far end the caller cannot read is dropped silently**, and if that
      leaves a kind empty the key disappears with it — a task whose only relation points
      at a forbidden task answers ``{}``, not ``{"related": []}``. Skipping this check
      turns ``related_tasks`` into a way to read the title, description and dates of any
      task by id.

    ⚠️ The ``prefetch`` branch below supplies the three inputs — the relation rows, the far
    end tasks, the permissions map — and **stops there**. The ``readable`` set and the
    filtering loop are computed the same way on both paths, deliberately: this is the one
    place in the read path where losing a line changes a performance problem into a
    disclosure. A batched variant that recomputed the verdict would be a second copy of
    the security rule, and the copies would be reviewed one file apart.
    """
    if prefetch is not None:
        relations = prefetch.relation_rows.get(task_id, [])
    else:
        relations = list(
            session.scalars(
                select(TaskRelation)
                .where(TaskRelation.task_id == task_id)
                .order_by(TaskRelation.id)
            )
        )
    if not relations:
        return {}

    if prefetch is not None:
        others = {
            relation.other_task_id: prefetch.far_end_tasks[relation.other_task_id]
            for relation in relations
            if relation.other_task_id in prefetch.far_end_tasks
        }
        permissions = prefetch.far_end_permissions
    else:
        others = {
            other.id: other
            for other in session.scalars(
                base_task_query().where(
                    Task.id.in_({relation.other_task_id for relation in relations})
                )
            )
        }
        permissions = max_permissions_for_projects(
            session, user_id, sorted({task.project_id for task in others.values()})
        )

    readable = {
        task_id_
        for task_id_, other in others.items()
        if permissions.get(other.project_id, NO_PERMISSION) != NO_PERMISSION
    }

    grouped: dict[str, Any] = {}
    for relation in relations:
        if relation.other_task_id not in readable:
            continue
        grouped.setdefault(relation.relation_kind, []).append(
            _nested_related_task(session, others[relation.other_task_id], user_id, prefetch)
        )
    return {kind: grouped[kind] for kind in sorted(grouped)}


class ReadPrefetch:
    """Every row :func:`read_view` needs for a whole page, fetched with ``IN (...)``.

    ⚠️ **This holds rows, never verdicts.** Each field below is the same raw material the
    single-task path selects, only gathered for many tasks at once; every rule that turns
    rows into a response — the ``None``-when-empty collections, the relation ordering, the
    alphabetical key order, and above all the **permission filter on relation far ends** —
    stays in the one function that already owns it. That is the whole point of threading a
    prefetch through ``read_view`` instead of growing a second, batched serialiser beside
    it: a rule that exists in two places is a rule that will exist in one of them.

    ``users`` is the exception to "authoritative": it memoises rather than enumerates,
    because user ids repeat heavily across a page (author, assignees, label creators,
    bucket creators) and callers outside this module hold ids the builder cannot see. A
    miss costs one query and is then cached, so a forgotten id degrades to the old cost
    rather than to a wrong answer. Everything else is keyed by task id and derived from
    exactly ``task_ids``, so absence there genuinely means "no rows" — which is why
    :func:`read_view` refuses a task the prefetch does not cover instead of reading an
    empty default as an answer.
    """

    __slots__ = (
        "assignee_rows",
        "attachment_files",
        "attachment_rows",
        "far_end_permissions",
        "far_end_tasks",
        "favorite_task_ids",
        "label_rows",
        "projects",
        "relation_rows",
        "reminder_rows",
        "task_ids",
        "users",
    )

    def __init__(
        self,
        *,
        task_ids: frozenset[int],
        projects: dict[int, Project],
        assignee_rows: dict[int, list[TaskAssignee]],
        attachment_rows: dict[int, list[TaskAttachment]],
        attachment_files: dict[int, File],
        label_rows: dict[int, list[Label]],
        relation_rows: dict[int, list[TaskRelation]],
        reminder_rows: dict[int, list[TaskReminder]],
        far_end_tasks: dict[int, Task],
        far_end_permissions: dict[int, int],
        favorite_task_ids: frozenset[int],
        users: dict[int, User | None],
    ) -> None:
        self.task_ids = task_ids
        self.projects = projects
        self.assignee_rows = assignee_rows
        self.attachment_rows = attachment_rows
        self.attachment_files = attachment_files
        self.label_rows = label_rows
        self.relation_rows = relation_rows
        self.reminder_rows = reminder_rows
        self.far_end_tasks = far_end_tasks
        self.far_end_permissions = far_end_permissions
        self.favorite_task_ids = favorite_task_ids
        self.users = users


def build_prefetch(session: Session, tasks: Sequence[Task], user_id: int) -> ReadPrefetch:
    """Gather in nine queries what :func:`read_view` would otherwise issue per task.

    The ordering of the grouped rows is load-bearing and is the reason each batch repeats
    the single-task query's ``order_by``: ``assignees`` follows the assignment row id and
    ``labels`` the ``label_tasks`` row id, both of which are insertion order rather than
    the target's id. Grouping in iteration order then preserves it per task, so the
    batched page serialises in the same order as a single read of the same task.

    Relation far ends are collected across the *whole page* as one set. They are looked up
    with ``base_task_query`` — a soft-deleted far end must stay invisible here exactly as
    it does on the single-task path — and their projects are resolved in one permission
    call. The verdict itself is not computed here; see :func:`_related_tasks`.
    """
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return ReadPrefetch(
            task_ids=frozenset(),
            projects={},
            assignee_rows={},
            attachment_rows={},
            attachment_files={},
            label_rows={},
            relation_rows={},
            reminder_rows={},
            far_end_tasks={},
            far_end_permissions={},
            favorite_task_ids=frozenset(),
            users={},
        )

    projects = {
        project.id: project
        for project in session.scalars(
            select(Project).where(Project.id.in_({task.project_id for task in tasks}))
        )
    }

    assignee_rows: dict[int, list[TaskAssignee]] = {}
    for row in session.scalars(
        select(TaskAssignee).where(TaskAssignee.task_id.in_(task_ids)).order_by(TaskAssignee.id)
    ):
        assignee_rows.setdefault(row.task_id, []).append(row)

    label_rows: dict[int, list[Label]] = {}
    for link_task_id, label in session.execute(
        select(LabelTask.task_id, Label)
        .join(Label, Label.id == LabelTask.label_id)
        .where(LabelTask.task_id.in_(task_ids))
        .order_by(LabelTask.id)
    ):
        label_rows.setdefault(link_task_id, []).append(label)

    relation_rows: dict[int, list[TaskRelation]] = {}
    for relation in session.scalars(
        select(TaskRelation).where(TaskRelation.task_id.in_(task_ids)).order_by(TaskRelation.id)
    ):
        relation_rows.setdefault(relation.task_id, []).append(relation)

    # Ordered by `reminder`, which is the single-task query's order too — see
    # `_reminders`. Grouping in iteration order then preserves it per task, so a page and
    # a single read of the same task serialise identically.
    reminder_rows: dict[int, list[TaskReminder]] = {}
    for reminder in session.scalars(
        select(TaskReminder)
        .where(TaskReminder.task_id.in_(task_ids))
        .order_by(TaskReminder.reminder)
    ):
        reminder_rows.setdefault(reminder.task_id, []).append(reminder)

    far_end_ids = {relation.other_task_id for rows in relation_rows.values() for relation in rows}
    far_end_tasks = {
        far_end.id: far_end
        for far_end in session.scalars(base_task_query().where(Task.id.in_(far_end_ids)))
    }
    far_end_permissions = max_permissions_for_projects(
        session, user_id, sorted({far_end.project_id for far_end in far_end_tasks.values()})
    )

    # A far end's ``is_favorite`` is filled in even though nothing else about it is, so
    # the favourite lookup has to cover both the page and everything it points at.
    favourite_candidates = set(task_ids) | far_end_tasks.keys()
    favorite_task_ids = frozenset(
        session.scalars(
            select(Favorite.entity_id).where(
                Favorite.entity_id.in_(favourite_candidates),
                Favorite.user_id == user_id,
                Favorite.kind == FAVORITE_KIND_TASK,
            )
        )
    )
    # Attachments: a task's own ``attachments`` array is in the read response by default
    # (no ?expand= involved), so the prefetch loads both the attachment rows and the file
    # rows they point at — upstream's ``addMoreInfoToTasks`` does this on every read.
    # Empty stays ``None`` on the wire (upstream only ever appends to a nil slice);
    # everything else here honours the same rule too.
    attachment_rows: dict[int, list[TaskAttachment]] = {}
    attachment_file_ids: set[int] = set()
    for attach in session.scalars(
        select(TaskAttachment)
        .where(TaskAttachment.task_id.in_(task_ids))
        .order_by(TaskAttachment.id)
    ):
        attachment_rows.setdefault(attach.task_id, []).append(attach)
        if attach.file_id:
            attachment_file_ids.add(attach.file_id)
    attachment_files: dict[int, File] = (
        {f.id: f for f in session.scalars(select(File).where(File.id.in_(attachment_file_ids)))}
        if attachment_file_ids
        else {}
    )
    # Attachment creators use the same user lookup as everyone else; the negative
    # link-share ids are resolved at view time, not here, because the wire shape's
    # pseudo-user builder needs the share row, and that row is per-subject not per-task.
    wanted_attachment_creators = {
        row.created_by_id for rows in attachment_rows.values() for row in rows
    }

    users: dict[int, User | None] = {}
    wanted = {task.created_by_id for task in tasks if task.created_by_id is not None}
    wanted |= {row.user_id for rows in assignee_rows.values() for row in rows}
    wanted |= {
        label.created_by_id
        for rows in label_rows.values()
        for label in rows
        if label.created_by_id is not None
    }
    wanted |= wanted_attachment_creators

    return ReadPrefetch(
        task_ids=frozenset(task_ids),
        projects=projects,
        assignee_rows=assignee_rows,
        attachment_rows=attachment_rows,
        attachment_files=attachment_files,
        label_rows=label_rows,
        relation_rows=relation_rows,
        reminder_rows=reminder_rows,
        far_end_tasks=far_end_tasks,
        far_end_permissions=far_end_permissions,
        favorite_task_ids=favorite_task_ids,
        users=users,
    )


def read_view(
    session: Session, task: Task, user_id: int, prefetch: ReadPrefetch | None = None
) -> TaskRead:
    """Assemble a task the way ``addMoreInfoToTasks`` does, for GET responses.

    The collection defaults here are the *read* half of the create/read split documented
    in ``schemas.task``: ``related_tasks`` is a map because upstream assigns one
    unconditionally (``tasks.go:807``), while ``assignees`` and ``labels`` stay ``None``
    when empty because upstream only ever *appends* to them. Swapping those is the single
    most likely way to get a passing test suite and a failing parity run.

    ``prefetch`` changes where the rows come from and nothing else — see
    :class:`ReadPrefetch`. Passing one that does not cover ``task`` is a programming error
    and raises, because every lookup below reads "absent" as "no rows": a page serialised
    against someone else's prefetch would quietly drop that task's assignees, labels and
    relations and still answer 200.
    """
    if prefetch is not None and task.id not in prefetch.task_ids:
        raise ValueError(
            f"task {task.id} is not covered by this prefetch; "
            "build_prefetch must be called with every task that will be serialised"
        )
    view = _columns(task).model_copy(
        update={
            "identifier": _identifier(_project(session, task.project_id, prefetch), task.index),
            "assignees": _assignees(session, task.id, prefetch),
            "attachments": _attachments(session, task.id, prefetch),
            "labels": _labels(session, task.id, prefetch),
            "reminders": _reminders(session, task.id, prefetch),
            "related_tasks": _related_tasks(session, task.id, user_id, prefetch),
            "is_favorite": _is_favorite(session, task.id, user_id, prefetch),
            "created_by": user_view(session, task.created_by_id, prefetch),
        }
    )
    return view


def _assignees(
    session: Session, task_id: int, prefetch: ReadPrefetch | None = None
) -> list[UserRead] | None:
    """Assigned users, or ``None`` — **not** ``[]`` — when there are none.

    Ordered by the assignment row id, which is insertion order. Upstream appends to a nil
    slice, so an unassigned task serialises ``"assignees": null`` while the *create*
    response for the same task says ``[]``; see ``schemas.task``.
    """
    if prefetch is not None:
        rows = prefetch.assignee_rows.get(task_id, [])
    else:
        rows = list(
            session.scalars(
                select(TaskAssignee)
                .where(TaskAssignee.task_id == task_id)
                .order_by(TaskAssignee.id)
            )
        )
    if not rows:
        return None

    # The two branches differ only in where the user rows come from. Without a prefetch it
    # is still one query for the whole task, as it always was — routing this through
    # ``user_view`` per row would have quietly turned a task's assignee list into its own
    # small N+1 on the single-task path while fixing the collection one.
    views = _user_views(session, [row.user_id for row in rows], prefetch)
    # An assignment whose user row has gone is dropped rather than serialised as null. Note
    # this yields ``[]``, not ``None``, when every row is dropped: the rows existed, which
    # is the same distinction upstream draws by appending to an already-allocated slice.
    return [view for view in (views.get(row.user_id) for row in rows) if view is not None]


def _attachments(
    session: Session, task_id: int, prefetch: ReadPrefetch | None = None
) -> list[dict[str, Any]] | None:
    """A task's own attachments, or ``None`` — **not** ``[]`` — when there are none.

    Upstream's ``addMoreInfoToTasks`` always runs an attachment scan on the read path, so
    a task with attachments carries them in the task list and a task without leaves the
    key absent (Go appends to a nil slice). The shape and the link-share pseudo-user are
    the attachment resource's own read shape — reusing its builder keeps nesting the wire
    signature of attachment rows in one place.

    File rows are loaded once per page in the prefetch; absent one, this falls back to a
    per-task lookup the same way ``_assignees`` does, so the single-task path is unchanged.
    """
    from calton.schemas.attachment import AttachmentRead
    from calton.services.attachment_service import _view as _attachment_view

    if prefetch is not None:
        rows = prefetch.attachment_rows.get(task_id, [])
        files = prefetch.attachment_files
    else:
        rows = list(
            session.scalars(
                select(TaskAttachment)
                .where(TaskAttachment.task_id == task_id)
                .order_by(TaskAttachment.id)
            )
        )
        file_ids = [r.file_id for r in rows if r.file_id]
        loaded = [session.get(File, fid) for fid in file_ids]
        files = {f.id: f for f in loaded if f is not None}
    if not rows:
        return None

    def _as_dict(row: TaskAttachment) -> dict[str, Any]:
        attachment = _attachment_view(session, row, files)
        # `attachments` on a read response carries only specific fields — measured.
        return AttachmentRead(
            id=attachment.id,
            task_id=attachment.task_id,
            created_by=attachment.created_by,
            file=attachment.file,
            created=attachment.created,
        ).model_dump(mode="json")

    return [_as_dict(row) for row in rows]


def _user_views(
    session: Session, user_ids: Sequence[int], prefetch: ReadPrefetch | None
) -> dict[int, UserRead | None]:
    """Resolve several user ids at once, one query per call — or none, given a prefetch.

    ``None`` values are kept in the mapping rather than omitted so that callers can tell
    "this id has no user row" from "this id was never asked for".
    """
    if prefetch is not None:
        return {user_id: user_view(session, user_id, prefetch) for user_id in user_ids}
    if not user_ids:
        return {}
    found = {
        user.id: user for user in session.scalars(select(User).where(User.id.in_(set(user_ids))))
    }
    return {
        user_id: (
            UserRead.model_validate(found[user_id], from_attributes=True)
            if user_id in found
            else None
        )
        for user_id in user_ids
    }


def _reminders(
    session: Session, task_id: int, prefetch: ReadPrefetch | None = None
) -> list[dict[str, Any]] | None:
    """A task's reminders, or ``None`` — **not** ``[]`` — when it has none.

    The read path had no reminders at all until now, which cost eight parity cases in the
    filter group: their filter semantics were already right — same result set, same count,
    same order, same pagination headers — and the only differing column was this one.

    Measured shape, all of it rather than the one line that showed up in the diff:

    * exactly three keys, ``reminder`` / ``relative_period`` / ``relative_to``. The row's
      ``id``, ``task_id`` and ``created`` are ``json:"-"`` upstream and must not appear.
    * a task with none serialises ``null``, matching ``assignees`` and ``labels`` and for
      the same reason — upstream only ever appends to a nil slice.
    * the item shape and the collection shape agree, which is *not* automatic here: the
      project pair has already diverged twice on exactly this question.
    * **ordered by ``reminder`` ascending, not by insertion.** Measured with a create
      carrying ``[Jun, Jan, Mar]``, which came back ``[Jan, Mar, Jun]``. The seed's own
      multi-reminder task (915) is already chronological, so it cannot tell the two
      orderings apart — an out-of-order, non-palindromic sample is what makes this
      assertion mean anything.
    * ``relative_to`` is ``""`` for an absolute reminder and a field name such as
      ``due_date`` for a relative one; ``relative_period`` is seconds and may be negative.

    ⚠️ Plain dicts, not a model. ``TaskRead.reminders`` is ``list[dict[str, Any]]`` because
    the **write** response echoes whatever the client sent, and that half lives in
    ``TaskWriteResponse``. Introducing a concrete model here would be fine for the read
    path and would have to be kept out of the write one; there is nothing to gain from it
    until reminders are writable.
    """
    if prefetch is not None:
        rows = prefetch.reminder_rows.get(task_id, [])
    else:
        rows = list(
            session.scalars(
                select(TaskReminder)
                .where(TaskReminder.task_id == task_id)
                .order_by(TaskReminder.reminder)
            )
        )
    if not rows:
        return None

    return [
        {
            "reminder": format_rfc3339(row.reminder),
            "relative_period": row.relative_period or 0,
            "relative_to": row.relative_to or "",
        }
        for row in rows
    ]


def _labels(
    session: Session, task_id: int, prefetch: ReadPrefetch | None = None
) -> list[LabelRead] | None:
    """Attached labels, or ``None`` when there are none — same nil-slice reasoning.

    Ordered by the ``label_tasks`` row id rather than by label id: measured, task 950 lists
    950, 951, 954 because that is the order the links were made in, and sorting by label id
    happens to give the same answer on that sample. Do not rely on it.

    No permission filter. A label created by someone else that is attached to a task the
    caller can read comes back in full — visibility follows the *task*, not the label's
    owner, which is the opposite of what ``PUT /labels`` enforces.
    """
    if prefetch is not None:
        rows = prefetch.label_rows.get(task_id, [])
    else:
        rows = list(
            session.execute(
                select(Label)
                .join(LabelTask, LabelTask.label_id == Label.id)
                .where(LabelTask.task_id == task_id)
                .order_by(LabelTask.id)
            ).scalars()
        )
    if not rows:
        return None

    creators = _user_views(
        session,
        [label.created_by_id for label in rows if label.created_by_id is not None],
        prefetch,
    )
    return [
        LabelRead(
            id=label.id,
            title=label.title,
            description=label.description or "",
            hex_color=label.hex_color or "",
            created_by=creators.get(label.created_by_id),
            created=label.created,
            updated=label.updated,
        )
        for label in rows
    ]


def _apply_assignees(session: Session, *, task_id: int, project_id: int, data: TaskWrite) -> bool:
    """``assignees`` on a task write is a **full replacement**, and omitting it clears.

    Returns whether the task **had any assignees before this write**, which is the fact
    :func:`_write_view` needs and the only moment it can still be observed.

    ⚠️ **Destructive, and that is upstream's behaviour rather than an oversight here.**
    ``updateSingleTask`` calls ``updateTaskAssignees(s, t, t.Assignees)`` unconditionally,
    and a body that carried no ``assignees`` key deserialises to a nil slice — which the
    replacement then treats as "the new set is empty". Measured: task 950 holding assignee
    901, given ``POST {"title": "…", "done": true}``, comes back 200 with **no assignees
    left**. ``"assignees": null`` and ``"assignees": []`` do the same thing.

    So a client doing a *partial* update silently unassigns everyone, while the
    read-modify-write client the MCP gate uses is safe precisely because it echoes the
    array back. Do not "fix" this: guarding it on ``data.assignees is not None`` makes
    every partial update diverge, and it is the kind of change that looks like a bug fix
    in review. If we ever want the safer behaviour it is a deliberate deviation with an
    entry in the register, not a quiet patch here.
    """
    ids = [entry.id for entry in (data.assignees or ())]
    return assignee_service.replace_assignees_for_task_write(
        session, task_id=task_id, project_id=project_id, assignee_ids=ids
    )


#: ``ReminderRelation`` (``task_reminder.go:40-42``). Anything else is "absolute".
_REMINDER_RELATIONS = {
    "due_date": "due_date",
    "start_date": "start_date",
    "end_date": "end_date",
}


def _apply_reminders(session: Session, task: Task, data: TaskWrite) -> list[dict[str, Any]] | None:
    """``updateReminders`` (tasks.go:1975) — a **full replacement**, and omitting clears.

    ⚠️ **Destructive, and it is upstream's behaviour rather than an oversight here.** Both
    write paths call this unconditionally, so a body carrying no ``reminders`` key
    deserialises to a nil slice and the replacement reads that as "the new set is empty".
    Measured with the three-read pattern — ``GET`` task 914, ``POST {"title": …}``, ``GET``
    again: upstream answers ``reminders: null`` **and the rows are gone**.

    ⚠️ The write *response* alone cannot see this. Both a correct and an ignoring
    implementation answer ``reminders: null`` to that update; they differ only on the third
    read. This function exists because that third read is what caught it — until the read
    path learned to serialise reminders at all, our stale rows were invisible and we
    matched upstream here **by accident**.

    Returns the rebuilt list in wire form, which is also what the response echoes: upstream
    answers ``t.Reminders`` *after* the rebuild, not the raw request. Measured: a create
    sending ``[Jun, Jan, Mar]`` answers ``[Jan, Mar, Jun]``, and a relative reminder whose
    anchor date is unset answers the zero time rather than what was sent.

    Three rules, all from ``updateRelativeReminderDates`` + the rebuild loop:

    * ``relative_to`` naming ``due_date`` / ``start_date`` / ``end_date`` computes
      ``anchor + relative_period`` seconds, and leaves the **zero time** when that anchor is
      itself unset — not an error.
    * a non-zero ``relative_period`` with no ``relative_to`` is **400/4022**.
    * duplicates collapse by whole-second timestamp (upstream keys a map on
      ``Reminder.UTC().Unix()``), and the result is sorted ascending.
    """
    requested = list(data.reminders or ())

    for row in session.scalars(select(TaskReminder).where(TaskReminder.task_id == task.id)):
        session.delete(row)
    session.flush()

    anchors = {
        "due_date": data.due_date,
        "start_date": data.start_date,
        "end_date": data.end_date,
    }

    resolved: dict[int, dict[str, Any]] = {}
    for entry in requested:
        if not isinstance(entry, dict):
            continue
        period = entry.get("relative_period") or 0
        relative_to = str(entry.get("relative_to") or "")
        if relative_to in _REMINDER_RELATIONS:
            anchor = anchors.get(relative_to) or ZERO_TIME
            when = ZERO_TIME if anchor == ZERO_TIME else anchor + timedelta(seconds=period)
        else:
            if period:
                raise CaltonError.from_name("models.ErrReminderRelativeToMissing")
            when = _as_datetime(entry.get("reminder")) or ZERO_TIME
        # Keyed on the whole second, matching Go's map on Reminder.UTC().Unix(): two
        # reminders in the same second are one reminder, and the later entry wins.
        resolved[int(when.timestamp())] = {
            "reminder": when,
            "relative_period": period,
            "relative_to": relative_to,
        }

    rows = sorted(resolved.values(), key=lambda item: item["reminder"])
    for item in rows:
        session.add(
            TaskReminder(
                task_id=task.id,
                reminder=item["reminder"],
                relative_period=item["relative_period"],
                relative_to=item["relative_to"],
            )
        )
    session.flush()

    if not rows:
        return None
    return [
        {
            "reminder": format_rfc3339(item["reminder"]),
            "relative_period": item["relative_period"],
            "relative_to": item["relative_to"],
        }
        for item in rows
    ]


def _as_datetime(value: Any) -> datetime | None:
    """A reminder timestamp out of a request dict, which may already be parsed.

    ``TaskWrite.reminders`` is ``list[dict[str, Any]]``, so the element values arrive
    exactly as JSON gave them — a string here, not a ``datetime``. Both are accepted
    because the bulk path hands over already-parsed values.
    """
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str) and value:
        parsed = parse_rfc3339(value)
        return as_utc(parsed) if isinstance(parsed, datetime) else None
    return None


def _write_view(
    session: Session,
    task: Task,
    user_id: int,
    data: TaskWrite,
    *,
    creating: bool,
    had_assignees: bool,
    reminders: list[dict[str, Any]] | None,
) -> TaskWriteResponse:
    """Assemble the response to a create or update.

    Not a re-read: ``updateSingleTask`` ends ``mergo.Merge(&ot, t, WithOverride)`` and then
    ``*t = ot``, so the response is the bound request struct with the stored row showing
    through wherever the request held a zero. Which fields actually fall back was measured
    rather than reasoned from mergo, because two of them do not follow it.

    **The measurement that settles it**: build a task whose every field holds a distinctive
    non-zero value, then ``POST {"title": …}`` and read the response. With a zero stored
    value "falls back" and "returns zero" are the same answer, so a task left at its
    defaults cannot tell them apart (practice 4 — most of the fields below look like
    fallbacks on such a task and are not).

    ==============  =====================  =====================================
    field           minimal update returns  reading
    ==============  =====================  =====================================
    ``index``        the stored ``8``       falls back
    ``project_id``   the stored ``950``     falls back (explicit in updateSingleTask)
    ``created``      ``0001-01-01``         **pure echo — does not fall back**
    ``created_by``   ``null``               **pure echo**
    ``identifier``   ``""``                 pure echo (``xorm:"-"``, never in ``ot``)
    ==============  =====================  =====================================

    ``created`` is the one that matters: an earlier version wrote
    ``data.created if data.created != ZERO_TIME else task.created``, and that ``else``
    branch has no counterpart upstream. It only ever looked right because the fixtures it
    was checked against carried a real ``created``, where echoing and falling back agree.

    ``created`` and ``created_by`` **do** carry real values on create, where the response is
    assembled after the insert rather than merged over it. Measured: creating with
    ``created: 1999-03-04`` answers with the server's own timestamp, while updating with the
    same body echoes 1999.

    ``assignees`` turns on neither create nor update but on **whether the task had any
    assignees before this write** — ``updateTaskAssignees`` deletes them and calls
    ``setTaskAssignees(nil)`` (a JSON ``null``) only when there were some to delete, and
    otherwise returns early leaving the empty slice it just built (a JSON ``[]``):

    ============================  ==================  ==========  ==========
    request                       had assignees?      response    stored
    ============================  ==================  ==========  ==========
    ``[{"id": 902}]``             either              echoed      ``{902}``
    omitted / ``[]``              **yes**             ``null``    cleared
    omitted / ``[]``              **no**              ``[]``      none
    ============================  ==================  ==========  ==========

    Create is just the third row, since a task being created has none — which is why this
    is not a ``creating`` branch. A previous version wrote it as one and answered ``null``
    where upstream answers ``[]`` for the update of a task that had no assignees; the
    sample it was taken from happened to have one, so both readings agreed on it.

    The echoed entries are the *parsed request objects*, not the stored rows — a body
    carrying ``{"id": 902}`` comes back with zero-valued ``username``/timestamps, and a body
    repeating an already-assigned id comes back with the repeat still in it while storage
    holds one row.
    """
    # `model_construct`, not `model_copy(update=)`: the class has to change to the write
    # model, and neither call validates — which is the point. The collections below hold
    # the client's own values, and validating them here would raise after the write has
    # already committed. `TaskWrite` refuses a bad element at bind time instead.
    return TaskWriteResponse.model_construct(
        **{
            **_columns(task).__dict__,
            # Echoed, not computed. A client that omits `identifier` gets "" back, while a
            # GET of the same task computes "#3" — measured, and pinned by
            # test_update_response_echoes_readonly_fields_without_persisting.
            "identifier": data.identifier,
            "index": data.index or task.index,
            "created": task.created if creating else data.created,
            "assignees": (data.assignees or (None if had_assignees else [])),
            "related_tasks": data.related_tasks,
            "labels": data.labels,
            # Dumped to plain objects: the field is declared `list[dict]` because that
            # is what upstream's swagger says, and handing pydantic a model where a
            # dict is declared raises a serializer warning — an error here, on a
            # response whose write has already committed.
            "attachments": (
                [entry.model_dump(mode="json") for entry in data.attachments]
                if data.attachments
                else data.attachments
            ),
            # The **rebuilt** list, not the request. Upstream answers `t.Reminders` after
            # `updateReminders` has resolved relative dates, deduplicated by whole second
            # and sorted ascending — measured: a create sending [Jun, Jan, Mar] answers
            # [Jan, Mar, Jun]. Echoing the request is right only for absolute reminders
            # sent in order, which is every sample the corpus happens to use.
            "reminders": reminders,
            "reactions": data.reactions,
            "bucket_id": data.bucket_id,
            "position": data.position,
            "is_favorite": _is_favorite(session, task.id, user_id),
            # Create resolves the doer; update echoes, so a client that omits it gets
            # `null` back even though the row has a creator. Measured both ways on the
            # same task in one session.
            "created_by": (user_view(session, task.created_by_id) if creating else data.created_by),
        }
    )


def create_task(
    session: Session, *, project_id: int, data: TaskWrite, user_id: int
) -> TaskWriteResponse:
    """``PUT /projects/{id}/tasks``. Note v1's inverted verbs: PUT creates.

    Validation order is upstream's and is observable: an empty title is rejected
    (400/4001) *before* the project is looked up, so a bad title against a missing project
    reports the title.
    """
    if not data.title:
        raise CaltonError.from_name("models.ErrTaskCannotBeEmpty")
    _validate_repeat_after(data.repeat_after)

    project = _project(session, project_id)
    if project is None:
        raise _project_not_found()

    task = Task(
        title=data.title,
        description=data.description,
        done=data.done,
        done_at=data.done_at,
        due_date=data.due_date,
        start_date=data.start_date,
        end_date=data.end_date,
        project_id=project_id,
        repeat_after=data.repeat_after,
        repeat_mode=data.repeat_mode,
        priority=data.priority,
        hex_color=data.hex_color,
        percent_done=data.percent_done,
        cover_image_attachment_id=data.cover_image_attachment_id,
        created_by_id=user_id,
    )
    _insert_with_index(session, task, preferred_index=data.index)

    if data.is_favorite:
        set_favorite(session, task.id, user_id, favorite=True)

    # Before the commit, so a rejected assignee takes the whole task with it. Measured:
    # `PUT /projects/950/tasks {"title": …, "assignees": [{"id": 99999}]}` answers
    # 404/1005 and the project's task count is unchanged — the task is not created.
    had_assignees = _apply_assignees(session, task_id=task.id, project_id=project_id, data=data)
    reminders = _apply_reminders(session, task, data)

    session.commit()
    session.refresh(task)

    view = _write_view(
        session,
        task,
        user_id,
        data,
        creating=True,
        had_assignees=had_assignees,
        reminders=reminders,
    )
    # Create computes the identifier rather than echoing it: `setIdentifier` runs at the
    # end of createTasks, after the index is known. Measured: the create response carries
    # "#8" while the update response for the same task carries "".
    return view.model_copy(update={"identifier": _identifier(project, task.index)})


def _insert_with_index(session: Session, task: Task, *, preferred_index: int) -> None:
    """Insert, allocating the per-project index and retrying if someone took it first.

    ``UQE_tasks_tasks_project_index`` is what actually guarantees uniqueness — the
    ``max(index) + 1`` read is not atomic, so two concurrent creators compute the same
    number and one of them loses the insert. Catching that and recomputing is the whole
    mechanism; without the retry the loser gets a 500.

    A caller-supplied index is honoured when free, matching ``setNewTaskIndexes``
    (the file importer depends on it). Measured: creating with ``index: 50`` keeps 50, and
    the next create gets 51.
    """
    for attempt in range(INDEX_ALLOCATION_ATTEMPTS):
        wanted = preferred_index if (preferred_index and attempt == 0) else 0
        task.index = wanted or _next_index(session, task.project_id)
        savepoint = session.begin_nested()
        try:
            session.add(task)
            savepoint.commit()
            return
        except IntegrityError:
            # Rolling the savepoint back expunges anything added inside it, so `task` is
            # transient again and the next `session.add` re-attaches it. Expunging it by
            # hand here raises "not present in this Session".
            savepoint.rollback()
    raise RuntimeError(
        f"could not allocate a task index in project {task.project_id} "
        f"after {INDEX_ALLOCATION_ATTEMPTS} attempts"
    )


def get_task(session: Session, task_id: int) -> Task:
    """The live task, or 404. Soft-deleted rows are indistinguishable from missing ones."""
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise _task_not_found()
    return task


def get_task_by_index(session: Session, project_id: int, index: int) -> Task:
    """``GET /projects/{project}/tasks/by-index/{index}``, resolved to a row.

    Upstream resolves ``(project, index)`` to a task id and then runs the ordinary
    ReadOne pipeline, so **the project is never checked in its own right**. Measured
    consequences, both of which a project-first implementation gets wrong:

    * a project that does not exist answers 404/4002 "This task does not exist" — the
      *task* error, not 3001;
    * a project the caller cannot read answers 403 only when the task is really there.

    Goes through ``base_task_query``, which is why a soft-deleted task's index reads as
    404 while the row still holds that index (corpus: ``task.by_index.out_of_range``).
    """
    task = session.scalars(
        base_task_query().where(Task.project_id == project_id, Task.index == index)
    ).one_or_none()
    if task is None:
        raise _task_not_found()
    return task


def apply_update(
    session: Session, *, task: Task, data: TaskWrite, user_id: int
) -> TaskWriteResponse:
    """Write ``data`` onto ``task`` and return the response view. **Does not commit.**

    Split out of ``update_task`` so the bulk endpoint (``services.task_bulk``) writes each
    task through exactly this code, and so the two cannot drift: the column semantics
    here — which fields survive a zero value and which do not — are the same contract in
    both, and a bulk-local reimplementation would be a second place to get mergo's rules
    wrong. Flushes so ``updated`` is allocated, but leaves the transaction open, which is
    what lets a bulk batch roll back as a unit.
    """
    _validate_repeat_after(data.repeat_after)

    was_done = bool(task.done)
    target_project_id = data.project_id or task.project_id

    dates = RepeatDates(data.due_date, data.start_date, data.end_date)
    done = data.done
    done_at = data.done_at if data.done_at != ZERO_TIME else task.done_at

    # updateDone (tasks.go:1909). Completing a repeating task reopens it on its next
    # occurrence instead of finishing it — omit this and the request still answers 200
    # while the user's recurring task silently disappears from their list.
    if not was_done and done:
        if is_repeating(task.repeat_after or 0, task.repeat_mode):
            dates = reschedule(task, dates, utcnow())
            done = False
        done_at = utcnow()
    elif was_done and not done:
        done_at = ZERO_TIME

    # title and project_id keep their stored value when the request sends zero; every
    # other column takes the request's value even when that value is zero.
    task.title = data.title or task.title
    task.description = data.description
    task.done = done
    task.done_at = done_at
    task.due_date = dates.due
    task.start_date = dates.start
    task.end_date = dates.end
    task.repeat_after = data.repeat_after
    task.repeat_mode = data.repeat_mode
    task.priority = data.priority
    task.hex_color = data.hex_color
    task.percent_done = data.percent_done
    task.cover_image_attachment_id = data.cover_image_attachment_id

    if target_project_id != task.project_id:
        # Moving projects re-allocates the index, because it is unique per project and the
        # old number is almost certainly taken in the destination. Measured: a task at
        # index 6 in one project arrives at index 1 in an empty one.
        task.project_id = target_project_id
        task.index = _next_index(session, target_project_id)

    set_favorite(session, task.id, user_id, favorite=data.is_favorite)

    # After the task's own columns and *before* the flush completes the request, so the
    # two either both land or neither does. Measured: a valid title with an unknown
    # assignee answers 404/1005 and the title is not written either.
    had_assignees = _apply_assignees(
        session, task_id=task.id, project_id=task.project_id, data=data
    )
    reminders = _apply_reminders(session, task, data)

    session.flush()
    session.refresh(task)

    return _write_view(
        session,
        task,
        user_id,
        data,
        creating=False,
        had_assignees=had_assignees,
        reminders=reminders,
    )


def update_task(
    session: Session, *, task_id: int, data: TaskWrite, user_id: int
) -> TaskWriteResponse:
    """``POST /tasks/{id}`` — full replacement, with the two exceptions named at the top."""
    task = get_task(session, task_id)
    view = apply_update(session, task=task, data=data, user_id=user_id)
    session.commit()
    return view


def delete_task(session: Session, *, task_id: int) -> None:
    """Soft delete: stamp ``deleted_at`` and leave the row in place.

    ``tasks`` is the only table upstream that does this. The row keeps its index, which is
    why ``_next_index`` counts deleted rows.
    """
    task = get_task(session, task_id)
    task.deleted_at = utcnow()
    session.commit()
