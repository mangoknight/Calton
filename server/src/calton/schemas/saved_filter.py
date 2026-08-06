"""Saved filter wire shapes.

Two things here are counterintuitive enough to be worth stating before the code, because
both are the kind of difference an implementation "improves" without noticing:

* **``owner`` is ``null`` on create and on update, and hydrated only on ``GET``.** The
  server knows perfectly well who the owner is — it just used the caller's identity to
  set it — and it still answers ``"owner": null``. Upstream's ``Create``/``Update`` never
  populate ``SavedFilter.Owner`` (``saved_filters.go``: the field is ``xorm:"-"``, filled
  only by the read path), and the JSON tag has no ``omitempty``, so the key is present
  and null. Hydrating it would be more useful, consistent with ``GET``, and a byte-level
  regression on two of the four endpoints. Note the direction is the **opposite** of task
  comments, where create is complete and update is not: there is no house rule to apply.

* **``filters`` is normalised to all five keys.** A request carrying only
  ``{"filter": "done = false"}`` comes back as
  ``{s, sort_by, order_by, filter, filter_include_nulls}`` with the four absent ones at
  their zero values, because Go unmarshals into a whole ``TaskCollection`` struct and
  marshals the struct back. Echoing the request's single key is the natural Python
  spelling and produces a four-key deficit that nothing but a byte comparison catches.

``created``/``updated`` on the update response are the **zero time**, not the row's real
values — see :class:`SavedFilterWriteResponse`.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field

from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserEcho, UserRead


class SavedFilterFilters(CaltonModel):
    """The ``TaskCollection`` subset a saved filter stores, as JSON.

    Five keys, and only five: ``TaskCollection`` also carries ``ProjectID``,
    ``ProjectViewID``, ``FilterTimezone`` and ``Expand``, all tagged ``json:"-"``
    (``task_collection.go:31-57``). Adding any of them here would put a key on the wire
    that upstream never sends and, worse, would persist it into the ``filters`` column,
    which is read back by ``GET`` and by every pseudo-project task query.

    ``sort_by``/``order_by`` default to **null**, not ``[]``. Measured on both the create
    and the read path; ``[]`` is what a ``default_factory=list`` would produce and it is a
    different JSON value.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    s: str = ""
    sort_by: list[str] | None = None
    order_by: list[str] | None = None
    filter: str = ""
    filter_include_nulls: bool = False

    def __bool__(self) -> bool:
        """Falsy when every field is at its zero value, as Go's ``required`` sees it.

        ⚠️ This is load-bearing, not a convenience. ``SavedFilterWrite.filters`` carries
        ``GoValid("required")``, and govalidator's ``required`` on a ``*TaskCollection``
        tests the **pointed-to struct** for zero-ness, not the pointer for nil. Measured on
        the reference server, and the three results only make sense together:

            {"filters": {}}                 -> 412 filters: non zero value required
            {"filters": {"filter": ""}}     -> 412 (same — an explicit zero is still zero)
            {"filters": {"s": "hello"}}     -> 201 (one non-zero field is enough)

        Python's default is that every model instance is truthy, so without this an empty
        ``filters`` object would sail through to a 201 storing a filter that matches
        everything — a strictly *more permissive* API than upstream, reachable by a client
        that sends the key without filling it in, which is what a form with an untouched
        filter field does.
        """
        return bool(
            self.s or self.sort_by or self.order_by or self.filter or self.filter_include_nulls
        )


class SavedFilterWrite(CaltonModel):
    """The body of ``PUT /filters`` and ``POST /filters/{filter}`` — the same shape.

    **``filters`` is required, on update as well as on create.** Upstream tags it
    ``valid:"required"`` (``saved_filters.go:36``) and the update path runs the same
    validation, so renaming a filter means resending the whole filter expression:
    ``POST /filters/950 {"title": "Renamed"}`` is **412**, not a partial update. Relaxing
    that on the update path is the single most natural courtesy to add here — "the user
    only wanted to rename it" — and it turns a 412 into either a 200 that wipes the
    expression or a 200 that silently keeps it. Both diverge, and the second one diverges
    invisibly.

    ``title`` carries ``required,runelength(1|250)``. The lower bound of the runelength
    never fires on its own because ``required`` reports first; it is transcribed anyway
    because the tag text is reproduced verbatim in ``invalid_fields``.

    ⚠️ Both fields need a **default** despite being "required" — Go decodes a missing key
    to the zero value and validates afterwards, so absent and empty are the same case and
    must produce ``"<field>: non zero value required"`` rather than Pydantic's ``missing``.

    ``description`` and ``is_favorite`` are writable and are **not** exempt from the
    whole-model replace: ``POST`` omitting ``description`` resets it to ``""`` (measured —
    set it to "D", then POST without it, then read back: ``""``).
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    # Declared in the Go struct's order. That order is *not* the order invalid_fields
    # comes back in — upstream builds that array from a map and Go randomises the walk,
    # so the corpus asserts it as a set (see core/errors.invalid_fields_of).
    # ⚠️ `validate_default=True` on both, and it is what makes `required` mean anything:
    # Pydantic does not run validators on a field that fell back to its default, so an
    # *omitted* key would skip the rule entirely and `PUT /filters {}` would answer 201.
    # The measured answer is 412 naming both fields. The default itself is also required —
    # see GoValid — so the two settings only work as a pair, and dropping either one turns
    # a 412 into a 201 or into Pydantic's `missing`, which reports different text.
    filters: Annotated[SavedFilterFilters | None, GoValid("required")] = Field(
        default=None, validate_default=True
    )
    title: Annotated[str, GoValid("required,runelength(1|250)")] = Field(
        default="", validate_default=True
    )
    description: str = ""
    is_favorite: bool = False
    #: Shadows the path segment on update — see ``core.crud_router._effective_key``.
    id: int = 0
    # Read-only, declared so the write response can hand them back. Neither is written.
    #
    # ⚠️ The update response used to emit `owner: null` and the zero `created`
    # unconditionally. That is what a body omitting them produces, so it looked right;
    # measured against a read-modify-write body — GET the filter, POST it back — upstream
    # answers with the full `owner` and the real `created`, because it is echoing the
    # struct it bound. `SavedFilter.Update` copies only `OwnerID` back onto the receiver
    # and the response serialises `Owner`, which is why re-reading the row does not
    # reproduce this either.
    owner: UserEcho | None = None
    created: Timestamp = ZERO_TIME


class SavedFilterRead(CaltonModel):
    """``GET /filters/{filter}`` — the only shape with a hydrated ``owner``.

    Key order follows the Go struct so the JSON reads the same way; it is not load-bearing
    (the harness compares parsed objects), but a diff against a recorded body is easier to
    read when it lines up.
    """

    id: int
    filters: SavedFilterFilters
    title: str = ""
    description: str = ""
    owner: UserRead | None = None
    is_favorite: bool = False
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class SavedFilterWriteResponse(SavedFilterRead):
    """What ``PUT /filters`` and ``POST /filters/{filter}`` answer.

    Identical field set to :class:`SavedFilterRead`; a separate class so that returning
    the hydrated one from a write path is a type error rather than a parity failure found
    much later. Two values differ, and both are **echoes of the request** rather than
    anything read off the row:

    * ``owner`` is whatever the body carried, and ``null`` when it carried nothing. The
      row's owner is never consulted — posting ``owner: {"id": 901}`` to a filter alice
      owns answers with bob and leaves the row alone.
    * on **update**, ``created`` is likewise the body's, so a body without one answers the
      zero time ``0001-01-01T00:00:00Z``. The row's ``created`` is untouched: reading the
      same filter back afterwards shows the original value. Do not conclude from the
      response that updating resets the creation date. (On **create**, ``created`` is
      real, because the insert set it.)

    ``owner`` is re-declared as :class:`UserEcho` rather than inherited as
    :class:`UserRead`: the value being handed back came out of a request body, where
    ``created``/``updated`` are optional, and validating it against the read model would
    500 **after the write has already committed**.
    """

    owner: UserEcho | None = None  # type: ignore[assignment]  # deliberate: a write response is not substitutable for a read one
