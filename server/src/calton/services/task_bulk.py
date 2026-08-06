"""``POST /tasks/bulk`` (T27).

Measured against the running Go reference server, not read off ``pkg/models/bulk_task.go``.
Four of the behaviours below are the opposite of what the endpoint's name and its swagger
annotation suggest, and none of them fails loudly when implemented the other way.

**1. The write is all-or-nothing, and only a read-back proves it.**
``updateTasks`` (tasks.go:1618) loops the ids and returns on the first error, but the
handler owns the transaction, so a failure part-way rolls the earlier writes back.
Measured with the good ids *first*: ``[925, 926, 99999]`` answers 404/4002 and 925 and
926 are unchanged afterwards. The natural implementation — validate and write each id as
you go — produces exactly the same status codes and leaves the batch half-applied. The
response cannot tell the two apart; ``test_bulk_rolls_back_the_whole_batch`` reads the
rows back.

**2. ``fields`` does not protect assignees, reminders or favourites.**
This is the expensive one. ``updateSingleTask`` restores the 14 *columns* not named in
``fields`` from the stored row, but assignees, reminders and the favourite flag are not
columns — they live in their own tables and are rewritten from ``values`` **regardless of
what ``fields`` says**. A body that carries none of them therefore clears all three
(``updateTaskAssignees``, task_assignees.go:77: an empty list means delete everything).
Measured on task 950:

    POST /tasks/bulk {"task_ids": [950], "fields": ["priority"], "values": {"priority": 3}}
    -> 200
    assignees   ['bob'] -> []          reminders  [one] -> None
    is_favorite  true   -> false       priority       0 -> 3

**and the response reports none of it** — ``tasks[0].assignees`` comes back ``null``
either way. A "bulk edit priority" button in a UI silently unassigns everyone.
Copied deliberately (practice: upstream quirks are reproduced, not corrected); if this is
ever to be changed it is a product decision and needs a deviation entry, not a patch here.

⚠️ **"Rewritten from ``values``" is not "always cleared", and the difference decides the
MCP acceptance path.** The real client updates read-modify-write: it GETs the whole task
and POSTs it back, and **all three read paths hydrate ``assignees``** (single GET,
``GET /tasks``, ``GET /projects/{id}/tasks`` — re-measured). So its echo carries the
array and upstream keeps the assignments; only a body that omits them clears. The set
replacement, its two gates, and how it differs from the assignee *bulk* endpoint live in
``assignee_service.replace_assignees_for_task_write``.

``attachments`` is the opposite and was measured alongside: it is echoed back too and
upstream does **nothing** with it — a fabricated entry creates no row and ``[]`` clears
none. Attachments are only ever written through the multipart upload endpoint (T32).

**3. Error precedence follows the order of ``task_ids``, because field validation is
inside the loop.** ``[924, 99999]`` with an invalid field name answers 400/4027 (924 is
reached first and validates the names), while ``[99999, 924]`` with the same body answers
404/4002. Validating ``fields`` once up front — the obvious tidy-up — gets the second case
wrong and nothing else changes.

**4. A batch naming only ids that do not exist is 400/4004, not 404/4002.**
``CanUpdate`` loads the rows first and raises ``ErrBulkTasksNeedAtLeastOne`` when it finds
none, so "no such task" and "no tasks given" collapse into one answer. A soft-deleted task
counts as absent: ``[921]`` alone is 4004, ``[924, 921]`` is 4002.

Gate order, measured, all four gates independently failable:

1. rows exist at all           -> 400/4004   (``[99999]``, ``[]``, ``{}``, ``[921]``)
2. caller may write every      -> 403/0      (``[924, 927]``, and ``[927]`` alone)
   project involved              "Forbidden"
3. destination project, when   -> 404/3001 missing, 403/0 forbidden
   ``values.project_id != 0``    — checked **even when ``project_id`` is not in
                                 ``fields``**, i.e. even when no task will move
4. per id, in order            -> 404/4002 unknown id, then 400/4027 bad field name
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.models import Project
from calton.models.task import Task, base_task_query
from calton.models.task_assignee import TaskAssignee
from calton.models.task_reminder import TaskReminder
from calton.permissions.project import can_write as project_can_write
from calton.schemas.bulk_task import BulkTaskRead
from calton.schemas.task import TaskWrite, TaskWriteResponse
from calton.schemas.user import UserRead
from calton.services import assignee_service, task_service

#: ``colsToUpdate`` (tasks.go:1283), in upstream's order. Naming anything outside this
#: list in ``fields`` is 400/4027 — including real task fields such as ``id`` and
#: ``is_favorite``, which are rejected even though the request can still change the
#: favourite through ``values``. Measured.
UPDATABLE_COLUMNS = (
    "title",
    "description",
    "done",
    "due_date",
    "repeat_after",
    "priority",
    "start_date",
    "end_date",
    "hex_color",
    "percent_done",
    "project_id",
    "bucket_id",
    "repeat_mode",
    "cover_image_attachment_id",
)


def _validate_columns(fields: list[str] | None) -> frozenset[str]:
    """The named columns, or the empty set meaning "all 14".

    Called per task inside the loop rather than once up front, which is the only way to
    reproduce upstream's error precedence — see point 3 in the module docstring.
    """
    if not fields:
        return frozenset()

    for name in fields:
        if name not in UPDATABLE_COLUMNS:
            raise CaltonError.from_name("models.ErrInvalidTaskColumn", column=name)
    return frozenset(fields)


def _effective_values(task: Task, values: TaskWrite, columns: frozenset[str]) -> TaskWrite:
    """``values`` with every column *not* named in ``fields`` restored from the row.

    This is ``updateSingleTask``'s ``if !fieldSet[...] { t.X = ot.X }`` block
    (tasks.go:1315-1355) expressed as data rather than as control flow, so the write below
    stays a single code path shared with the un-scoped case. An empty ``columns`` means
    ``fields`` was absent or ``[]``, and upstream treats both as "write all 14" — the
    difference between them survives only in the echo.
    """
    if not columns:
        return values

    restored: dict[str, object] = {}
    if "title" not in columns:
        restored["title"] = task.title
    if "description" not in columns:
        restored["description"] = task.description or ""
    if "done" not in columns:
        restored["done"] = bool(task.done)
        restored["done_at"] = task.done_at
    if "due_date" not in columns:
        restored["due_date"] = task.due_date
    if "repeat_after" not in columns:
        restored["repeat_after"] = task.repeat_after or 0
    if "priority" not in columns:
        restored["priority"] = task.priority or 0
    if "start_date" not in columns:
        restored["start_date"] = task.start_date
    if "end_date" not in columns:
        restored["end_date"] = task.end_date
    if "hex_color" not in columns:
        restored["hex_color"] = task.hex_color or ""
    if "percent_done" not in columns:
        restored["percent_done"] = task.percent_done or 0
    if "project_id" not in columns:
        restored["project_id"] = task.project_id
    if "repeat_mode" not in columns:
        restored["repeat_mode"] = task.repeat_mode
    if "cover_image_attachment_id" not in columns:
        restored["cover_image_attachment_id"] = task.cover_image_attachment_id or 0
    # `bucket_id` is deliberately absent: it is `xorm:"-"`, so naming it in Cols() writes
    # nothing. Bucket membership lives in task_buckets and belongs to T28.

    return values.model_copy(update=restored)


def _apply_associations(
    session: Session, task_id: int, project_id: int, user_id: int, values: TaskWrite
) -> list[UserRead] | None:
    """Rewrite assignees, reminders and the favourite flag from ``values``.

    Returns what ``tasks[i].assignees`` must echo, which is **three-valued** and is not
    derivable from the resulting set alone — measured, all three reachable:

    ==========================  ==================  =========================
    request                     set before          echo
    ==========================  ==================  =========================
    ``assignees`` absent        non-empty           ``null``   (it cleared one)
    ``assignees`` absent        empty               ``[]``     (nothing to clear)
    ``assignees: []``           non-empty           ``null``
    ``assignees: [{id: 901}]``  anything            the list, **unhydrated**
    ==========================  ==================  =========================

    ``null`` and ``[]`` both mean "no assignees now"; which one appears depends on
    whether there was something to delete. That falls straight out of
    ``updateTaskAssignees``: the delete branch calls ``setTaskAssignees(nil)``, while the
    nothing-to-do branch returns with the empty slice already assigned. Computing the
    echo from the final state instead collapses the two and is wrong half the time.

    "Unhydrated" is the other half: the echoed users carry the requested ``id`` and zero
    values everywhere else — ``{"id": 901, "name": "", "username": "", "created":
    "0001-01-01T00:00:00Z", ...}`` — because this is the request struct, not a re-read.

    Point 2 of the module docstring: these three are **not** protected by ``fields``,
    because ``fields`` only ever restores columns. They are rewritten from ``values``
    whatever ``fields`` says — so a scoped edit that carries none of them clears all
    three. Reproduced on purpose.

    ⚠️ **"Rewritten", not "cleared" — and the difference is the whole read-modify-write
    path.** An earlier version of this cleared assignees unconditionally, on the
    measurement that ``GET /tasks/{id}`` answers ``assignees: null``. That measurement was
    taken **after an earlier probe in the same run had already wiped the assignees**, so
    it described a task with none rather than the endpoint's shape. Re-measured properly:
    all three read paths — single GET, ``GET /tasks``, ``GET /projects/{id}/tasks`` —
    hydrate ``assignees``, so the real client's echo carries the array back and upstream
    keeps it. Clearing unconditionally would drop every assignee on the one code path the
    MCP acceptance line actually uses.
    """
    # Read the set *before* writing: the echo depends on whether there was one, and after
    # the write that information is gone.
    had_assignees = bool(
        session.scalars(select(TaskAssignee).where(TaskAssignee.task_id == task_id)).first()
    )

    if values.assignees:
        assignee_service.replace_assignees_for_task_write(
            session,
            task_id=task_id,
            project_id=project_id,
            assignee_ids=[entry.id for entry in values.assignees],
        )
        # Echoed verbatim: whatever the client sent for each user comes back unchanged,
        # so a full read-modify-write body round-trips and an id-only body echoes zeros.
        echo: list[UserRead] | None = [
            UserRead.model_validate(entry.model_dump()) for entry in values.assignees
        ]
    else:
        # Absent and empty are the same instruction here (task_assignees.go:77) — both
        # delete everything. They differ only in the echo, and only via `had_assignees`.
        session.execute(delete(TaskAssignee).where(TaskAssignee.task_id == task_id))
        echo = None if had_assignees else []

    if not values.reminders:
        session.execute(delete(TaskReminder).where(TaskReminder.task_id == task_id))
    task_service.set_favorite(session, task_id, user_id, favorite=values.is_favorite)
    return echo


def _authorise(session: Session, task_ids: list[int], values: TaskWrite, user_id: int) -> None:
    """``BulkTask.CanUpdate`` (bulk_task.go:36). Gates 1-3 of the module docstring."""
    tasks = session.scalars(base_task_query().where(Task.id.in_(task_ids))).all()
    if not tasks:
        # Reached for an empty list, an absent key, ids that do not exist, and ids that
        # are only soft-deleted. All four are 400/4004, not 404.
        raise CaltonError.from_name("models.ErrBulkTasksNeedAtLeastOne")

    for project_id in {int(task.project_id) for task in tasks}:
        if not project_can_write(session, user_id, project_id):
            raise ForbiddenError()

    # The destination is checked whenever `values.project_id` is set, regardless of
    # whether `project_id` is in `fields` — so a request that will not move anything can
    # still be refused for where it *would* have moved it. Measured: fields=["priority"]
    # with values.project_id pointing at another user's project is 403 and writes nothing.
    if values.project_id:
        destination = session.get(Project, values.project_id)
        if destination is None:
            raise CaltonError.from_name("models.ErrProjectDoesNotExist")
        if not project_can_write(session, user_id, values.project_id):
            raise ForbiddenError()


def bulk_update(
    session: Session,
    *,
    task_ids: list[int] | None,
    fields: list[str] | None,
    values: TaskWrite | None,
    user_id: int,
) -> BulkTaskRead:
    """Update every task in ``task_ids``, or none of them.

    ``values=None`` becomes an empty ``TaskWrite`` (``bt.Values = &Task{}``,
    bulk_task.go:82), which is why ``{"task_ids": [n], "fields": ["priority"]}`` sets that
    task's priority to **0** rather than leaving it alone: the column is named, so its
    zero value is written.
    """
    ids = list(task_ids or [])
    effective_values = values if values is not None else TaskWrite()

    _authorise(session, ids, effective_values, user_id)

    updated: list[TaskWriteResponse] = []
    for task_id in ids:
        # Duplicated ids are not de-duplicated and are not an error — measured:
        # `[925, 925]` is 200 and simply applies the same write twice. (Contrast the
        # assignee bulk endpoint, where a repeated id fails the whole request.)
        task = task_service.get_task(session, task_id)
        columns = _validate_columns(fields)
        scoped = _effective_values(task, effective_values, columns)

        assignee_echo = _apply_associations(
            session, task_id, int(task.project_id), user_id, effective_values
        )
        view = task_service.apply_update(session, task=task, data=scoped, user_id=user_id)
        # `apply_update` returns the shape the *single* update endpoint answers, which
        # hardcodes `assignees: []` because that endpoint does not touch assignees yet.
        # Bulk does, so the echo is computed above and applied here rather than in the
        # shared helper -- overriding it there would give T18 an echo for a clearing it
        # never performs.
        updated.append(
            view.model_copy(
                update={
                    "assignees": assignee_echo,
                    # mergo skips a zero-length slice, so an empty `attachments` leaves
                    # the stored (nil) value and echoes null -- while a non-empty one
                    # survives into the response. Measured: `attachments: []` in,
                    # `attachments: null` out. `_write_view` passes the list straight
                    # through, which turns [] into [].
                    # Dumped, like `_write_view` does: the response model declares
                    # `list[dict]`, and handing it parsed models raises a serializer
                    # warning — which is an error here, after the batch has committed.
                    "attachments": (
                        [entry.model_dump(mode="json") for entry in effective_values.attachments]
                        if effective_values.attachments
                        else None
                    ),
                }
            )
        )

    # One commit for the whole batch. Anything raised above leaves the session
    # uncommitted, and `get_db`'s context manager rolls it back — which is what makes the
    # batch atomic. Committing per task would pass every status-code assertion in this
    # module's tests and still corrupt the batch.
    session.commit()

    return BulkTaskRead(
        task_ids=task_ids,
        fields=fields,
        values=_echo_values(effective_values),
        tasks=updated,
    )


def _echo_values(values: TaskWrite) -> TaskWriteResponse:
    """``values`` as upstream echoes it: the bound struct, serialised whole.

    Not the result of anything — a ``title`` that was gated out of every task still comes
    back here.

    A straight dump, because ``TaskWrite`` now carries every field the read-modify-write
    client sends: ``assignees`` and ``created_by`` as ``UserEcho`` (parsed, so the whole
    nested user round-trips instead of collapsing to its id) and ``attachments`` as
    ``AttachmentEcho``. Rebuilding any of them from their ids here is what made a full
    RMW body come back with ``username: ""`` and zeroed timestamps where upstream echoes
    what it was given. ``null`` and ``[]`` stay distinct throughout.
    """
    # The write model, not the read one: this is the request struct handed back, so its
    # collections hold whatever the client sent. `TaskRead` now types them concretely and
    # would reject the echo *after* the batch committed.
    return TaskWriteResponse.model_validate(values.model_dump())
