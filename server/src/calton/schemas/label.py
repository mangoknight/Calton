"""Label wire shapes.

Three different response shapes come out of this one resource, and none of them is
"the label" in the same sense:

* ``GET``/``PUT``/``POST /labels`` return a fully hydrated label, ``created_by`` included
* ``PUT /tasks/{task}/labels`` returns exactly ``{label_id, created}`` — not the label,
  not the task
* ``POST /tasks/{task}/labels/bulk`` echoes the **request**, unhydrated: empty titles,
  null ``created_by``, zero timestamps, in the order submitted

Hydrating the latter two would be more useful and is wrong. They are separate types here
so that returning the wrong one is a type error rather than a parity failure discovered
much later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import ConfigDict, Field

from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class LabelWrite(CaltonModel):
    """The body of ``PUT /labels`` and ``POST /labels/{label}``.

    **``title`` is not required.** ``PUT /labels`` with an empty title, or with no body at
    all, answers 201 upstream — unlike projects and tasks, which reject it. Adding a
    ``required`` here would look like fixing an oversight and would break parity. The
    absence of *that* rule is the requirement; see ``tests/unit/test_label_contract.py``.

    ⚠️ It does carry the **upper** bound, though: ``label.go:34`` tags it
    ``runelength(1|250)`` and a 251-character title is measured 412/2002 with
    ``"title: qqq… does not validate as runelength(1|250)"``. The lower bound of that same
    rule never fires because govalidator skips a zero value unless ``required`` is also
    present — which is precisely why the empty title is a 201 and the long one is not.
    Reading "no required" as "no validation" drops the upper bound.

    Note also that upstream's notion of "required" is non-zero rather than non-blank, so a
    whitespace-only title is accepted. Nothing here may ``strip()`` before testing
    emptiness.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    #: ⚠️ **A body ``id`` shadows the path segment**, and that is upstream's behaviour, not
    #: a convenience. Echo binds path parameters *before* the body, so the body wins.
    #: Measured: ``POST /labels/950`` carrying ``{"id": 951}`` edits **951** and leaves
    #: 950 untouched, answering ``"id": 951``. Dropping it (which ``extra="ignore"`` did
    #: silently) makes us edit the object named in the path — a *different row* — while
    #: answering 200 either way. There is no status code that distinguishes the two.
    id: int = 0
    title: Annotated[str, GoValid("runelength(1|250)")] = ""
    description: str = ""
    hex_color: Annotated[str, GoValid("runelength(0|7)")] = ""


class LabelRead(CaltonModel):
    """A hydrated label, as the label endpoints return it.

    ``created_by`` is the ordinary five-field embedded user — ``UserRead``, the same one
    tasks and projects embed — and not a two-field abbreviation. The corpus asserts this
    object byte for byte (``label.read_one.ok``): ``name`` is present and empty, ``created``
    and ``updated`` are present, and ``email`` is absent because upstream blanks it before
    embedding. A label is readable by anyone who can see a task it is attached to, so the
    absent ``email`` is a privacy boundary, not a formatting detail.
    """

    id: int
    title: str = ""
    description: str = ""
    hex_color: str = ""
    created_by: UserRead | None = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class LabelAttached(CaltonModel):
    """``PUT /tasks/{task}/labels`` — two keys, deliberately, in both directions.

    Returning the hydrated label instead would break nothing a client depends on
    functionally, which is exactly why only a byte-level comparison catches it.

    **``label_id`` must not be required.** An empty request body is a real case with a
    measured answer — 403, because upstream ends up looking for "label 0" and refusing
    access to whatever it finds. Marking the field required instead makes FastAPI answer
    **412/2002** before any of that runs, and that is the single most natural way to write
    this schema in Python. ``tasklabel.add.label_id_zero_is_403_not_404`` is the only
    thing standing between the two.

    ``strict`` is on for the same reason it is on every write schema: ``{"label_id":
    "952"}`` is a 400/2004 upstream (``encoding/json`` refuses it), and Pydantic's default
    lax mode would coerce it and attach the label.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    label_id: int = 0
    created: Timestamp = ZERO_TIME


class LabelReference(CaltonModel):
    """One entry of the bulk request, and of the bulk response.

    The response echoes these back unhydrated, so the defaults here are what the client
    sees: empty strings, null ``created_by``, zero timestamps.
    """

    id: int = 0
    title: str = ""
    description: str = ""
    hex_color: str = ""
    created_by: UserRead | None = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class LabelBulk(CaltonModel):
    """``POST /tasks/{task}/labels/bulk`` in both directions.

    An **empty list clears every label** rather than meaning "nothing to do". Short
    circuiting on an empty list is a natural optimisation that leaves the response body
    correct — ``{"labels": []}`` either way — so only a database-side check catches it.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    labels: list[LabelReference] = Field(default_factory=list)


def hydrated(
    label_id: int,
    title: str,
    description: str,
    hex_color: str,
    created_by: UserRead | None,
    created: datetime,
    updated: datetime,
) -> LabelRead:
    """Build the full response shape the label endpoints use."""
    return LabelRead(
        id=label_id,
        title=title,
        description=description,
        hex_color=hex_color,
        created_by=created_by,
        created=created,
        updated=updated,
    )
