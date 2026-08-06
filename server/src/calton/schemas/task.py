"""Task request and response schemas.

Everything here is pinned to responses measured on the Go reference server (built by
``scripts/build_go_reference.sh``, commit recorded in ``reference.env``), not to a reading
of ``pkg/models/tasks.go``. Two of the pins contradict what the design documents say, so
they are called out where they are enforced.

**The empty-collection fields are not consistent, and must not be made consistent.**
Upstream fills them on different code paths, and the paths disagree:

    field           create (PUT)   read (GET)
    assignees       []             null
    related_tasks   null           {}
    labels          null           null
    attachments     null           null
    reminders       null           null
    reactions       null           null

``assignees`` and ``related_tasks`` *swap* between the two. On create, ``createTasks``
calls ``updateTaskAssignees``, which assigns an empty slice (``task_assignees.go:72``),
while nothing touches ``RelatedTasks``, leaving it nil. On read, ``addMoreInfoToTasks``
does the reverse: it only ever *appends* assignees (so nil survives) but assigns
``make(RelatedTaskMap)`` unconditionally (``tasks.go:807``).

The design doc's table describes the **create** response only and was written up as a
property of the model. Implementing it that way — the natural
``assignees: list = Field(default_factory=list)`` — makes every *read* wrong, and nothing
fails until the parity harness runs. So the defaults live on the assembly path in
``services.task_service``, not here: every collection field defaults to ``None`` and the
producing code path states what it means.

**The update response is not a re-read of the row.** It is the request object merged over
the stored row, so a client that echoes a read-only field back gets its own value
returned while the database keeps the real one (measured: ``index: 99`` in, ``index: 99``
out, ``index: 1`` on the next GET). ``TaskWrite`` therefore accepts the read-only fields
rather than dropping them — they are needed to reproduce the echo.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import ConfigDict, model_validator

from calton.db.types import (
    ZERO_TIME,
    GoFloat,
    OmitEmptyCollection,
    OmitEmptyPtr,
    OmitZeroTimestamp,
    Timestamp,
)
from calton.schemas.attachment import AttachmentEcho
from calton.schemas.base import CaltonModel
from calton.schemas.bucket_summary import BucketSummary
from calton.schemas.label import LabelRead
from calton.schemas.task_comment import TaskCommentRead
from calton.schemas.user import UserEcho, UserRead

#: ``tasks.go:55`` — ten years. Anything above it is 400/4029, and a negative value never
#: reaches the check because the ``valid:"range(0|…)"`` tag rejects it at 412/2002 first.
MAX_TASK_REPEAT_AFTER_SECONDS = 10 * 365 * 24 * 3600


class TaskRead(CaltonModel):
    """A task on the wire. Field order follows Go's struct so the two can be eyeballed.

    Fields carrying ``OmitEmptyPtr``/``OmitEmptyCollection`` are the expand-only ones:
    absent unless the request asked for them (T24 fills them in). Absent means the key
    is *gone*, not null — see ``test_expand_fields_are_absent_not_null``.
    """

    id: int
    title: str = ""
    description: str = ""
    done: bool = False
    done_at: Timestamp
    due_date: Timestamp
    # Owned by the reminders task; assembled as None here so an unset value is `null`.
    #: `dict` rather than `Any` so the element type reaches the contract as
    #: `object`, which is what upstream declares. An untyped element generates
    #: `any[]` on the frontend — no type protection at all on this field.
    #: Not a concrete model yet because nothing populates it; it is echoed from
    #: the request until reminders are implemented.
    #: ``list[Any]``, same reason as ``labels`` and ``attachments`` below.
    reminders: list[dict[str, Any]] | None = None
    project_id: int
    repeat_after: int = 0
    repeat_mode: int = 0
    priority: int = 0
    start_date: Timestamp
    end_date: Timestamp
    assignees: list[UserRead] | None = None
    #: ``LabelRead``, because this is now the **read** model only: the write response is
    #: :class:`TaskWriteResponse` below, which keeps plain objects here.
    #:
    #: This used to be ``list[Any]`` precisely because one model served both, and
    #: ``_write_view`` assigns the client's raw label dicts onto it — a concrete item type
    #: then made pydantic raise **after the write had already committed**, so the task was
    #: updated and the client got a 500 and retried. The trigger was not a malformed
    #: request: a plain ``GET`` followed by ``POST`` of the same body was enough, which is
    #: what a read-modify-write client does and what our own frontend never does.
    #: ``tests/unit/test_rmw_round_trip.py`` pins all three shapes.
    labels: list[LabelRead] | None = None
    hex_color: str = ""
    percent_done: GoFloat = 0
    #: ``{project.identifier}-{index}``, or ``#{index}`` when the project has none.
    #: Computed on the read path only — the update response echoes whatever was sent,
    #: which is the empty string for a client that omits it.
    identifier: str = ""
    index: int = 0
    related_tasks: dict[str, Any] | None = None
    #: Object elements. Nothing on the read path fills this in yet (T32 owns the read
    #: side); the write response echoes what was sent, and does so through
    #: :class:`TaskWriteResponse`.
    attachments: list[dict[str, Any]] | None = None
    cover_image_attachment_id: int = 0
    is_favorite: bool = False
    created: Timestamp
    updated: Timestamp
    #: Not a column: which bucket a task sits in is per-view and lives in task_buckets.
    #: Only non-zero when the task came through a view endpoint (T23/T28).
    bucket_id: int = 0
    position: GoFloat = 0
    reactions: dict[str, Any] | None = None
    created_by: UserRead | None = None

    # Present in JSON only when set. deleted_at is `omitzero` upstream; the rest are
    # `omitempty` pointers populated by ?expand=.
    # Not Optional: the key is dropped by comparing against the zero time, so a None here
    # would serialise as `null` and stay in the body — the exact bug this must avoid.
    deleted_at: OmitZeroTimestamp = ZERO_TIME
    is_unread: Annotated[bool | None, OmitEmptyPtr()] = None
    comment_count: Annotated[int | None, OmitEmptyPtr()] = None
    time_entries_count: Annotated[int | None, OmitEmptyPtr()] = None
    subscription: Annotated[dict[str, Any] | None, OmitEmptyPtr()] = None
    # ``buckets``, ``comments`` and ``comment_count`` are filled by ?expand= (T24).
    #
    # ``is_unread`` and ``time_entries_count`` are still declared-but-unfilled: their
    # tables (``task_unread_statuses``, ``time_entries``) are not among Phase 1's 24, so
    # there is nothing to read. ``subscription`` likewise waits on the subscription
    # endpoints. Accepting the expand value without populating the field is what upstream
    # does when there is no row either — measured: ?expand=is_unread answers 200 and the
    # key stays absent — so the two agree today and will diverge only once those tables
    # arrive.
    #
    # They stay declared because the contract diff (AC-2) compares response *field names*
    # against upstream's swagger, and a field the schema never mentions reads as "we
    # dropped it", indistinguishable from a real regression. OmitEmptyCollection keeps the keys
    # absent at runtime, so declaring them costs nothing on the wire.
    buckets: Annotated[list[BucketSummary] | None, OmitEmptyCollection()] = None
    comments: Annotated[list[TaskCommentRead] | None, OmitEmptyCollection()] = None


class TaskWrite(CaltonModel):
    """A task as clients send it, for both create and update.

    ``strict=True`` is mandatory (``CRUDRouter._require_strict``) and is what makes
    ``{"done": "yes"}`` a 400 rather than a silently stored ``True``. Measured: Go answers
    400/2004 for both ``{"done": "yes"}`` and ``{"priority": "3"}``.

    Every field has its Go zero value as the default, deliberately — **not**
    ``exclude_unset``. An omitted field must be indistinguishable from one sent as its
    zero value, because that is what full replacement means and the service layer decides
    per field whether zero writes or preserves.
    """

    model_config = ConfigDict(strict=True)

    id: int = 0
    title: str = ""
    description: str = ""
    done: bool = False
    done_at: Timestamp = ZERO_TIME
    due_date: Timestamp = ZERO_TIME
    start_date: Timestamp = ZERO_TIME
    end_date: Timestamp = ZERO_TIME
    project_id: int = 0
    repeat_after: int = 0
    repeat_mode: int = 0
    priority: int = 0
    hex_color: str = ""
    percent_done: GoFloat = 0
    cover_image_attachment_id: int = 0
    is_favorite: bool = False
    # Everything below is read-only, computed, or owned by another endpoint. It is
    # accepted for one reason: the update response echoes back whatever the client sent
    # (mergo overrides with any non-zero source field), so reproducing the echo requires
    # parsing them. None of them is written to the tasks table.
    #
    # Dropping them instead would still answer 200 — `extra="ignore"` sees to that — but
    # every read-modify-write client would get a response that disagrees with upstream's
    # in five or six fields at once.
    index: int = 0
    identifier: str = ""
    created: Timestamp = ZERO_TIME
    #: Bucket membership is per-view and lives in task_buckets; acting on this is T28.
    bucket_id: int = 0
    position: GoFloat = 0
    related_tasks: dict[str, Any] | None = None
    #: Object elements, so a non-object is refused while binding rather than while
    #: serialising. Upstream refuses it too, and refuses it *first*: measured,
    #: ``{"labels": ["x"]}`` is 400/2004 on both create and update and **nothing is
    #: written**. Declaring ``list[Any]`` here and tightening the response instead puts the
    #: rejection after the commit — the row changes and the client gets a 500.
    labels: list[dict[str, Any]] | None = None
    #: Read-only on every write path. Measured on the reference server: posting a
    #: fabricated entry creates nothing, and posting ``[]`` clears nothing — attachments
    #: are only ever written through the multipart upload endpoint (T32). Typed rather
    #: than ``list[Any]`` because the echo is the *parsed struct* re-serialised: a client
    #: sending two keys gets five back. See ``AttachmentEcho``.
    attachments: list[AttachmentEcho] | None = None
    reminders: list[dict[str, Any]] | None = None
    reactions: dict[str, Any] | None = None
    #: ⚠️ **Not** read-only, unlike its neighbours here — this one is acted on.
    #: ``updateSingleTask`` calls ``updateTaskAssignees`` with whatever the body carried,
    #: and "carried nothing" means "delete them all". So a client that echoes the array
    #: back keeps its assignees and one that omits it loses them, on the same endpoint.
    #: Only ``id`` is acted on; every other key is round-tripped verbatim, which is why
    #: this is ``UserEcho`` and not ``UserRead`` (required timestamps) or an id-only model
    #: (would echo zeros where the client sent a whole user).
    assignees: list[UserEcho] | None = None
    #: Echoed, never acted on — but it has to be *parsed* to be echoed faithfully: the
    #: read-modify-write client posts the whole nested user object back.
    created_by: UserEcho | None = None
    #: Same: read-only, and echoed back in a bulk request's ``values``. ``tasks[i]`` keeps
    #: the row's real timestamp, so this only ever surfaces in the request echo.
    updated: Timestamp = ZERO_TIME

    @model_validator(mode="before")
    @classmethod
    def _null_means_zero(cls, data: Any) -> Any:
        """Drop explicit nulls so the field default applies.

        ``encoding/json`` leaves a non-pointer Go field at its zero value when the JSON
        holds ``null``; it is not an error. Measured: ``{"description": null,
        "priority": null}`` is a 200 that stores ``""`` and ``0``. Without this, strict
        mode would answer 400 for a body upstream accepts — and read-modify-write clients
        send nulls constantly.
        """
        if not isinstance(data, dict):
            return data
        return {key: value for key, value in data.items() if value is not None}


class TaskWriteResponse(TaskRead):
    """What ``PUT /projects/{p}/tasks`` and ``POST /tasks/{id}`` answer.

    Same field set as the read shape. Three collections are re-declared with plain
    ``object`` elements because on a write they are **the client's own values handed
    back** — ``_write_view`` assigns ``data.labels`` / ``data.attachments`` /
    ``data.reminders`` straight through, and none of them is stored.

    Keeping the concrete read types here instead is the change that looks like tightening
    and is not: validation would run **after the write has committed**, so the row changes,
    the client gets a 500, and a retry does the work twice. That ordering is the whole
    reason the two models exist separately rather than one loose model serving both — the
    read half was loose for years to protect the write half, which cost the generated
    TypeScript its element types on every task read.

    The request schema is what refuses a bad element now, and it refuses it *before* the
    write: ``TaskWrite.labels`` takes objects, so ``{"labels": ["x"]}`` is a 400 at bind
    time. Measured on the reference service — upstream answers 400/2004 there too, and
    leaves the task untouched.
    """

    labels: list[dict[str, Any]] | None = None  # type: ignore[assignment]  # deliberate: a write response is not substitutable for a read one
    attachments: list[dict[str, Any]] | None = None
    reminders: list[dict[str, Any]] | None = None
    #: ``UserEcho`` rather than ``UserRead`` for the same reason, and it is not cosmetic:
    #: these hold the *parsed request* objects, so a declared ``UserRead`` makes pydantic
    #: emit a serializer warning — which this project raises as an error, i.e. a 500 on a
    #: request that had already committed. The declared type has to match what the write
    #: path actually assigns, not what the read path would.
    assignees: list[UserEcho] | None = None  # type: ignore[assignment]  # deliberate: a write response is not substitutable for a read one
    created_by: UserEcho | None = None  # type: ignore[assignment]  # deliberate: a write response is not substitutable for a read one
