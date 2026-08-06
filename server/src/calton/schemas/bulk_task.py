"""Request and response bodies for ``POST /tasks/bulk`` (T27).

**The response is the request envelope, not a list of tasks.** Upstream's swagger says
``@Success 200 {array} models.Task``; the handler is ``WebHandler.UpdateWeb``, which
serialises the bound ``BulkTask`` struct, so what actually comes back is
``{task_ids, fields, values, tasks}``. Measured on the reference server — an
implementation that returns a bare array is what the annotation asks for and is wrong.

Three echo details are load-bearing and none of them is guessable:

* ``fields`` distinguishes ``null`` from ``[]``. Omitting the key echoes ``null``,
  sending ``[]`` echoes ``[]``, and **both mean the same thing to the writer** (the
  default 14-column set). The observable difference is only in the echo, which is
  exactly the kind of distinction a "normalise it to a list" default destroys.
* ``values`` is echoed as a **fully hydrated zero-value task**, even when the request
  omitted it entirely or sent ``null``. It is the *input* serialised, not the result:
  a ``values.title`` that was never written to any task still comes back here.
* ``tasks[]`` carries the same read-only-field echo as a single update — a client that
  sends ``identifier: "ZZZ"`` gets ``"ZZZ"`` back while the row keeps its own. See
  ``services.task_service._write_view``.
"""

from __future__ import annotations

from pydantic import ConfigDict

from calton.schemas.base import CaltonModel
from calton.schemas.task import TaskWrite, TaskWriteResponse


class BulkTaskWrite(CaltonModel):
    """``POST /tasks/bulk``.

    Every field is optional at this layer. Declaring ``task_ids`` required would make
    FastAPI answer its own validation error for ``{}``, where upstream answers
    400/4004 "Need at least one tasks to do bulk editing." — measured, and 4004 is a
    business error the service raises after it has looked for the rows.

    ``strict`` keeps ``{"task_ids": "nope"}`` from being coerced; measured, that body is
    400/2004 upstream, which is what a bind failure maps to here.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    task_ids: list[int] | None = None
    fields: list[str] | None = None
    values: TaskWrite | None = None


class BulkTaskRead(CaltonModel):
    """The 200 body: the request envelope with ``tasks`` filled in.

    ``tasks`` is ``json:"tasks,omitempty"`` upstream, so an empty list would drop the key
    — unreachable in practice, because a request that names no existing task never gets
    this far (it is 400/4004). Modelled as always-present rather than omit-empty so the
    contract diff has something to compare; if a path is ever found that returns zero
    tasks with a 200, this is the line that has to change.
    """

    task_ids: list[int] | None = None
    fields: list[str] | None = None
    # The bulk response is a write response: every task in it went through
    # `_write_view`, so its collections hold the request's own values.
    values: TaskWriteResponse
    tasks: list[TaskWriteResponse]
