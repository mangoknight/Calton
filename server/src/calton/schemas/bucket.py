"""Bucket as it appears in the polymorphic collection response.

``tasks`` is ``omitempty`` upstream, so **an empty bucket has no ``tasks`` key at all** —
not ``null``, not ``[]``. Both halves of that matter:

* the bucket itself must still be returned. Dropping empty buckets ("a column with no
  cards need not be sent") makes empty columns vanish from the board, and the user can no
  longer drag anything into them;
* the key must be absent. ``bucket.tasks === undefined`` and ``=== null`` are different
  tests in the frontend, and Python emits a key for either ``[]`` or ``None``.

That makes four different spellings of "empty" now in play across this API — ``labels``
uses ``null``, ``related_tasks`` uses ``{}``, ``GET /tasks/{t}/labels`` uses ``[]``, and a
bucket's ``tasks`` omits the key. They are not converging; each one is measured where it
is produced.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from calton.db.types import ZERO_TIME, GoFloat, GoValid, OmitEmptyCollection, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.task import TaskRead
from calton.schemas.user import UserEcho, UserRead


class BucketRead(CaltonModel):
    """A bucket in the polymorphic collection response, carrying its page of tasks.

    ⚠️ **Declares every field itself instead of extending :class:`BucketSummary`, and that
    is about key order, not style.** Upstream's struct puts ``Tasks`` *between*
    ``project_view_id`` and ``limit`` (kanban.go:30-58). Pydantic appends a subclass's
    fields after the base's, so inheriting would emit ``tasks`` last — and there is no way
    to reorder it back, because redeclaring an inherited field keeps the base's position.

    JSON object order is not something most comparisons notice, and this one only
    surfaced through an oblique symptom: the parity harness walks the body collecting
    ``position`` values in document order, so a bucket carrying tasks produced
    ``[bucket_pos, task_pos, …]`` on our side against ``[task_pos, bucket_pos, …]`` on
    Go's. It reads as "the ordering is wrong" — pointing at sorting, which was fine — when
    the values and the sort were both correct and only the key order differed.

    ``BucketSummary`` keeps its own order, which already matches upstream for the
    endpoints that never carry tasks.
    """

    id: int
    title: str = ""
    project_view_id: int
    #: ``omitempty`` upstream, so **an empty bucket has no ``tasks`` key at all** — not
    #: ``null``, not ``[]``. Both halves matter: the bucket must still be returned
    #: (dropping empty buckets makes empty columns vanish from the board and nothing can
    #: be dragged into them), and the key must be *absent*, because
    #: ``bucket.tasks === undefined`` and ``=== null`` are different tests in the frontend
    #: while Python emits a key for either ``[]`` or ``None``.
    tasks: Annotated[list[TaskRead] | None, OmitEmptyCollection()] = None
    limit: int = 0
    #: The bucket's **total** task count, which does not shrink when ``per_page``
    #: truncates ``tasks``. Two numbers, deliberately: "50 of 60".
    count: int = 0
    position: GoFloat = 0
    created: Timestamp
    updated: Timestamp
    created_by: UserRead | None = None


class BucketWrite(CaltonModel):
    """The body of ``PUT .../buckets`` and ``POST .../buckets/{bucket}``.

    **Every field here defaults, and that is load-bearing on the update path.** Upstream's
    ``Bucket.Update`` (kanban.go:348) writes ``Cols("title", "limit", "position")``
    unconditionally, so a ``POST`` carrying only ``title`` stores **zero** into the other
    two. Measured on bucket 951, which the seed gives ``limit=2, position=200``::

        POST .../buckets/951  {"title": "Renamed"}
        before: (951, 'Doing',   limit=2, position=200.0)
        after:  (951, 'Renamed', limit=0, position=0.0)

    Both columns really are cleared in the database, not merely omitted from the response
    — a later read of the bucket list returns ``position: 0`` where the seed had 100.
    Defaulting these to "leave the stored value alone" (i.e. implementing ``POST`` as a
    patch) is the natural reading of a body that mentions one field, and it is wrong in a
    way nothing shouts about: the request still answers 200 with the same body, and the
    only visible difference is that a bucket silently keeps a capacity cap the user just
    cleared. ``bucket.update.clearing_limit_actually_disables_the_cap`` is the case that
    separates the two implementations.

    ⚠️ The corpus file's prose used to say ``position`` was only missing from the response
    and still present in the database. It is not; that was corrected against the reference
    server. ``created`` and ``created_by`` are the two that really are response-only
    omissions — both survive in the row.
    """

    # `valid:"required"` on kanban.go:35 — the tag text is reproduced verbatim inside
    # invalid_fields, so a paraphrase is a wire difference. Measured: an empty title is
    # 412/2002 with ["title: non zero value required"].
    #
    # Defaulted rather than required for the same reason ProjectWrite.title is: Go decodes
    # a missing key to the zero value and validates afterwards, so "absent" and "empty"
    # are one case upstream and both must report "non zero value required". validate_default
    # is what makes a body with no title at all reach the validator.
    title: Annotated[str, GoValid("required")] = Field(default="", validate_default=True)
    limit: Annotated[int, GoValid("range(0|9223372036854775807)")] = 0
    position: GoFloat = 0
    # Read-only, and declared for one reason: the write response is the bound request
    # struct, so whatever the client sent comes back. None of the three is written.
    #
    # ⚠️ Emitting fixed zeros here instead is right for a body that omits them and wrong
    # for the one shape that matters — read-modify-write. Measured: GET the bucket and
    # POST the same body back, and upstream answers with `created: 2026-01-01` and the
    # full `created_by`, because it echoed what it was handed. A minimal-body test cannot
    # tell the two implementations apart, since there the echo *is* zero.
    count: int = 0
    created: Timestamp = ZERO_TIME
    created_by: UserEcho | None = None
