"""Saved filters: loading, permissions, and the four operations.

**Only the owner may do anything with a saved filter.** There is no sharing, no team
grant and no permission table — ``canDoFilter`` (``saved_filters_permissions.go:52-66``)
loads the row and compares ``owner_id`` to the caller, and every one of read, update and
delete goes through it. That makes this the simplest permission model in the API, and it
is worth not generalising the project machinery onto it: a saved filter that appeared
under a project grant would expose one user's filter expressions to their collaborators.

**Existence is checked before ownership, and the two answer differently.** A missing
filter is 404/11001; an existing one belonging to somebody else is 403. So this resource
*does* disclose which ids exist — the opposite of labels, where both cases are an
identical 403 precisely so ids cannot be enumerated. Both are measured on the reference
server; neither generalises to the other.

The refusal **message** then splits by verb, which is a third axis:

* read (``GET /filters/{filter}``, and ``GET /projects/-N-1``) → ``"You don't have the
  permission to see this"``, code 0
* write (``POST``/``DELETE``) → ``"Forbidden"``, code 0

Same resource, same caller, same 403 status, two bodies. Collapsing them to one message
is invisible in every test that only looks at the status.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError, UnauthorizedError
from calton.core.policy import FORBIDDEN_READ_MESSAGE, ForbiddenError
from calton.db.base import utcnow
from calton.db.types import ZERO_TIME
from calton.filters.parser import parse_task_filter
from calton.models.saved_filter import SavedFilter
from calton.models.user import User
from calton.permissions.pseudo import project_id_from_saved_filter_id
from calton.schemas.saved_filter import (
    SavedFilterFilters,
    SavedFilterRead,
    SavedFilterWrite,
    SavedFilterWriteResponse,
)
from calton.schemas.user import UserRead
from calton.services.task_sort import parse_sort

#: ``CanRead`` returns ``PermissionAdmin`` unconditionally
#: (``saved_filters_permissions.go:25-28``) — it does not vary with the caller, because by
#: the time it is consulted the caller is already known to be the owner. Measured as
#: ``x-max-permission: 2`` on ``GET /projects/-951``.
SAVED_FILTER_MAX_PERMISSION = 2

#: The title upstream gives the filter it creates for every new account, and the
#: expression it stores. ``{username}`` is interpolated with the *username*, not the id —
#: measured on a fresh registration: ``done = false && assignees = eve1``.
DEFAULT_FILTER_TITLE = "My Open Tasks"
DEFAULT_FILTER_EXPRESSION = "done = false && assignees = {username}"


def user_id_of(auth: Any) -> int:
    """The caller's id, or the 401 the middleware would have sent.

    Never a fallback to a default user: these handlers are the only gate on a resource
    whose whole permission model is "are you the owner", so a subject of ``None`` reaching
    the comparison would make every filter writable by anyone.
    """
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def _not_found() -> CaltonError:
    return CaltonError.from_name("models.ErrSavedFilterDoesNotExist")


def load_for_read(session: Session, user_id: int, filter_id: int) -> SavedFilter:
    """The filter, or 404 if it does not exist / 403 with the *read* message if it is not
    yours."""
    stored = session.get(SavedFilter, filter_id)
    if stored is None:
        raise _not_found()
    if stored.owner_id != user_id:
        raise ForbiddenError(FORBIDDEN_READ_MESSAGE)
    return stored


def load_for_write(session: Session, user_id: int, filter_id: int) -> SavedFilter:
    """The filter, or 404 / 403 with the *write* message. See the module docstring."""
    stored = session.get(SavedFilter, filter_id)
    if stored is None:
        raise _not_found()
    if stored.owner_id != user_id:
        raise ForbiddenError()
    return stored


def stored_filters(stored: SavedFilter) -> SavedFilterFilters:
    """The five-key object out of the ``filters`` column.

    The column holds a serialised ``TaskCollection``. A row written by an older Calton —
    or by a test fixture — may hold the bare expression string instead, which
    ``json.loads`` either rejects or turns into a ``str``; both fall back to treating it as
    the expression rather than raising, because a 500 here would take out the project list
    as well (the pseudo projects are assembled from every filter the user owns).
    """
    raw = stored.filters or ""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        return SavedFilterFilters(filter=str(raw))
    return SavedFilterFilters.model_validate(payload)


def serialise_filters(filters: SavedFilterFilters) -> str:
    """The ``filters`` column's value.

    All five keys are written, including the null ones. A row missing keys deserialises to
    zero values rather than failing, so a partial write is not an error — it is a filter
    that quietly loses its sort order the next time it is read.
    """
    return json.dumps(filters.model_dump(mode="json"))


def _owner_view(session: Session, owner_id: int) -> UserRead | None:
    user = session.get(User, owner_id)
    return None if user is None else UserRead.model_validate(user, from_attributes=True)


def read_view(session: Session, stored: SavedFilter) -> SavedFilterRead:
    """``GET`` shape: the only one with a hydrated ``owner``."""
    return SavedFilterRead(
        id=stored.id,
        filters=stored_filters(stored),
        title=stored.title,
        description=stored.description or "",
        owner=_owner_view(session, stored.owner_id),
        is_favorite=bool(stored.is_favorite),
        created=stored.created,
        updated=stored.updated,
    )


def write_view(
    stored: SavedFilter, *, created_is_real: bool, echo: SavedFilterWrite | None = None
) -> SavedFilterWriteResponse:
    """``PUT``/``POST`` shape: the bound request struct, not a re-read of the row.

    ``created_is_real`` is not a formatting switch — it is the difference between the two
    write paths. ``Create`` answers a struct the insert has just populated, so its
    ``created`` is the real one; ``Update`` answers the struct it validated, and that
    struct only ever held what the client sent.

    ⚠️ ``owner`` and ``created`` on the update path are **echoes, not constants.** They
    used to be hardcoded to ``None`` and the zero time, which is the correct answer for a
    body that omits them — and therefore agrees with the echo on every request our own
    frontend makes. Measured on a read-modify-write body (GET the filter, POST it back
    unchanged): upstream answers with the full ``owner`` object and the real ``created``.

    Note this is **not** something a re-read would reproduce either. ``SavedFilter.Update``
    does re-read the row, but copies only ``OwnerID`` onto the receiver, while the response
    serialises the ``Owner`` struct — so a request naming a different owner echoes *that*
    one back even though the row is untouched. Measured: posting ``owner: {"id": 901}``
    answers with bob, and the row still belongs to alice.
    """
    echoed_owner = echo.owner if echo is not None else None
    echoed_created = echo.created if echo is not None else ZERO_TIME
    return SavedFilterWriteResponse(
        id=stored.id,
        filters=stored_filters(stored),
        title=stored.title,
        description=stored.description or "",
        owner=echoed_owner,
        is_favorite=bool(stored.is_favorite),
        created=stored.created if created_is_real else echoed_created,
        updated=stored.updated,
    )


def validate_expression(filters: SavedFilterFilters) -> None:
    """Reject a filter that the task query could not run, before it is stored.

    ⚠️ **A saved filter's expression is validated at write time, not only at read time.**
    ``SavedFilter.Create`` (``saved_filters.go:130``) calls
    ``getTaskFiltersFromFilterString`` before the insert, and ``Update`` does the same, so
    ``PUT /filters {"filters": {"filter": "x"}}`` is **400/4024** rather than a 201 that
    stores an expression which explodes the first time the pseudo project is opened.
    Skipping this is not a missing error message — it is a filter the user can save and
    then never read.

    The three refusals, all measured, and the order between them matters because a body can
    be wrong in more than one way at once:

    1. the expression itself — 4024 (parse) / 4016 (unknown field) / 4019 (bad value)
    2. then the sort keys, order before field: ``sort_by: ["nope"], order_by: ["sideways"]``
       answers **4014** (the order), not 4016 (the field)

    ⚠️ ``order_by`` alone is **not** validated: ``{"filter": "done = false", "order_by":
    ["sideways"]}`` is a **201** and stores ``"sideways"``. Orders are consumed positionally
    per ``sort_by`` entry, so with no sort keys nothing ever looks at them. Validating the
    list on its own would reject a body upstream accepts — which is why this delegates to
    ``parse_sort`` rather than checking the two lists separately.
    """
    parse_task_filter(filters.filter)
    parse_sort(filters.sort_by or [], filters.order_by or [])


def create_filter(
    session: Session, *, owner_id: int, body: SavedFilterWrite, with_views: bool = True
) -> SavedFilter:
    """Insert the row and give its pseudo project the four default views.

    ⚠️ **Creating a saved filter creates project views.** Measured: ``PUT /filters``
    adds four rows to ``project_views`` with ``project_id = id * -1 - 1`` and three buckets
    on the Kanban one, exactly as creating a real project does. Skipping it leaves
    ``GET /projects/-N-1`` answering ``views: []`` where upstream sends four, and leaves the
    board views of a saved filter unreachable.

    ``body.filters`` is not None by the time this runs — the schema's ``required`` rule has
    already turned that into a 412 — but it is defaulted here rather than asserted, so a
    caller reaching this from a unit test cannot produce a ``NoneType`` crash instead of an
    empty filter.
    """
    # Imported here rather than at module scope: project_service imports this module for
    # the registration default filter, and a top-level import closes the cycle.
    from calton.services.project_service import create_default_views

    filters = body.filters or SavedFilterFilters()
    validate_expression(filters)

    now = utcnow()
    stored = SavedFilter(
        title=body.title,
        description=body.description,
        filters=serialise_filters(filters),
        owner_id=owner_id,
        is_favorite=body.is_favorite,
        created=now,
        updated=now,
    )
    session.add(stored)
    # The pseudo project id is derived from the row id, so the views cannot be built
    # before the insert has allocated one.
    session.flush()

    if with_views:
        # ⚠️ `with_list_filter=False` — saved_filters.go:142 passes createDefaultListFilter
        # as false. The saved filter *is* the filter, so adding `done = false` to its List
        # view would hide the user's done tasks from the only view that shows them.
        # Measured: Go leaves this column NULL for a filter and fills it for a project.
        create_default_views(
            session,
            project_id_from_saved_filter_id(stored.id),
            owner_id,
            with_list_filter=False,
        )
        session.flush()

    return stored


def update_filter(session: Session, stored: SavedFilter, body: SavedFilterWrite) -> SavedFilter:
    """Whole-model replacement. Every field the body omits is reset to its zero value.

    ``owner_id`` and ``created`` are not in that set: they are not on the write schema at
    all, so a read-modify-write client that sends the ``owner`` object it read back cannot
    reassign the filter to somebody else.
    """
    filters = body.filters or SavedFilterFilters()
    validate_expression(filters)

    stored.title = body.title
    stored.description = body.description
    stored.is_favorite = body.is_favorite
    stored.filters = serialise_filters(filters)
    stored.updated = utcnow()
    session.flush()
    return stored


def delete_filter(session: Session, stored: SavedFilter) -> None:
    """Delete the row — and **only** the row.

    ⚠️ The four ``project_views`` and three ``buckets`` created alongside it are left
    behind, pointing at a ``project_id`` that no longer resolves to anything. That is
    upstream's behaviour (``SavedFilter.Delete`` deletes from ``saved_filters`` and nothing
    else), measured: after deleting a filter created through the API, its four view rows
    are still there. Cascading them is the obvious tidy-up and would make Calton's database
    diverge from a Go one under the same request sequence, which is what
    ``harness/schema_diff`` and every ``assert_sql`` case compare.

    ⛔ **This is upstream's behaviour, deliberately copied — it is not an oversight here,
    and it is not a bug to fix.** Anyone reading this function will see orphaned rows and
    reach for a cascade; that change is a deviation from Calton and needs the deviation
    register, not a tidy-up commit. ``test_saved_filters.py::
    test_deleting_the_filter_leaves_the_views_behind`` asserts the orphans are still there
    and will fail on the "fix" — that failure is the point, not a broken test.
    """
    session.delete(stored)
    session.flush()


def filters_owned_by(session: Session, user_id: int) -> list[SavedFilter]:
    return list(
        session.scalars(
            select(SavedFilter).where(SavedFilter.owner_id == user_id).order_by(SavedFilter.id)
        )
    )


def create_default_filter_for(session: Session, user: User) -> SavedFilter:
    """The "My Open Tasks" filter every new account gets.

    Measured on a fresh ``POST /register`` against the reference server: a
    ``saved_filters`` row appears with this title, ``description: ""``, ``is_favorite``
    false, and ``filters`` holding ``done = false && assignees = <username>`` — and the new
    user's ``GET /projects`` therefore lists a pseudo project for it alongside their Inbox.

    ⚠️ The expression interpolates the **username**, not the user id. The filter DSL
    resolves ``assignees = eve1`` by username, so an id here yields a filter that matches
    nothing — and matching nothing is indistinguishable from "you have no open tasks",
    which is the correct answer for a brand-new account. It would look right for as long as
    anyone bothered to check.

    Ordering: upstream creates the Inbox project **first** and this second, which is
    visible in the view ids (the Inbox's four are allocated before this one's four).
    ``create_default_project_for`` therefore calls this at its end.
    """
    return create_filter(
        session,
        owner_id=user.id,
        body=SavedFilterWrite(
            title=DEFAULT_FILTER_TITLE,
            description="",
            is_favorite=False,
            filters=SavedFilterFilters(
                filter=DEFAULT_FILTER_EXPRESSION.format(username=user.username)
            ),
        ),
    )
