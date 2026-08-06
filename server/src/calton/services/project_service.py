"""Project create/update/delete (``pkg/models/project.go``).

Three things here are not what the shape of the code suggests, and each of them is a
place where a reasonable implementation is silently wrong.

**Update writes a column whitelist, not the model.** ``project.go:1256-1270`` lists the
columns to write, and two of them are conditional. ``parent_project_id`` is only written
when the pointer is non-nil, and ``description`` is only written **when it is non-empty**.
So a description, once set, can never be cleared — not by omitting it, not by sending
``null``, and not by sending ``""``, because the empty string *is* the "leave it alone"
signal. That exception is not a pointer field, so an exception list built by scanning
pointer types misses it entirely. The same whitelist is why echoing ``owner_id``,
``created`` or ``updated`` back does not 422: those columns are simply never written.
Nothing in the schema layer is protecting them.

**Reparenting is guarded three times, not once** (CVE-2026-35595, CVE-2026-55064). The
gates only engage when ``parent_project_id`` was sent *and* the value actually changes:

===========================================  =========================================
situation                                    required
===========================================  =========================================
omitted or null                              ordinary write access
sent, but equal to the current parent        ordinary write access
changed to a positive id (attach)            Admin on the moved project **and** on the
                                             new parent
changed to 0 (detach to the top level)       Admin on the moved project
===========================================  =========================================

Implementing only the "can I write to the new parent" check leaves the vulnerability
open: a user with write access could attach a shared project under a project they own
and thereby inherit Admin over it.

**Delete is a fully recursive hard delete, and the recursion does not re-check
permissions.** A child owned by somebody else is deleted along with its parent. Adding a
permission check inside the recursion is the intuitive thing to do and diverges from
upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, aliased

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.models.bucket import Bucket
from calton.models.project import Project
from calton.models.project_view import ProjectView
from calton.models.saved_filter import Favorite
from calton.models.task import Task
from calton.models.team import ProjectUser, TeamProject
from calton.models.user import User
from calton.permissions.project import can_write, is_admin

#: Columns ``UpdateProject`` writes unconditionally (``project.go:1257-1263``).
#: Everything absent from this list is unreachable through the update endpoint, which is
#: what makes read-only fields read-only.
UNCONDITIONAL_UPDATE_COLUMNS = (
    "title",
    "is_archived",
    "identifier",
    "hex_color",
    "position",
)

#: Added only when the caller sent a non-nil pointer (``project.go:1266-1268``).
PARENT_UPDATE_COLUMN = "parent_project_id"

#: Added only when the value is non-empty (``project.go:1269-1271``). Not a pointer, which
#: is why scanning for pointer fields does not find this exception.
DESCRIPTION_UPDATE_COLUMN = "description"

#: Written only while updating the background, which P1 does not do.
BACKGROUND_UPDATE_COLUMNS = ("background_file_id", "background_blur_hash")


class ProjectViewKind:
    """``project_view.go``. Integers in the database, strings on the wire (T17)."""

    LIST = 0
    GANTT = 1
    TABLE = 2
    KANBAN = 3


class BucketConfigurationMode:
    NONE = 0
    MANUAL = 1
    FILTER = 2


@dataclass(frozen=True)
class DefaultView:
    title: str
    view_kind: int
    position: float
    filter: str | None = None
    bucket_configuration_mode: int = BucketConfigurationMode.NONE


#: The three buckets a Kanban view is created with (``project_view.go:346-392``), in
#: order. The first becomes the view's default bucket and the last its done bucket, which
#: is why they are created before the view is updated with those ids.
DEFAULT_BUCKETS = (
    ("To-Do", 100.0),
    ("Doing", 200.0),
    ("Done", 300.0),
)


#: The four views every new project gets (``project_view.go:523-580``). The List view
#: carries a filter; only Kanban is manually bucketed.
DEFAULT_VIEWS = (
    DefaultView("List", ProjectViewKind.LIST, 100, filter="done = false"),
    DefaultView("Gantt", ProjectViewKind.GANTT, 200),
    DefaultView("Table", ProjectViewKind.TABLE, 300),
    DefaultView(
        "Kanban",
        ProjectViewKind.KANBAN,
        400,
        bucket_configuration_mode=BucketConfigurationMode.MANUAL,
    ),
)


def recalculate_project_positions(session: Session, parent_project_id: int) -> None:
    """Spread every sibling evenly across the position space (``project.go``).

    Triggered when an update leaves a position below 0.1 — which an omitted position
    does, because Go's field is a plain float64 whose zero value is 0. The siblings are
    renumbered ``2^32 / count * (index + 1)`` in their current order.

    ⚠️ **NULL is not matched, and that is the whole behaviour of this function.**
    ``recalculateProjectPositions`` (``project.go:1326``) selects with a bare
    ``Where("parent_project_id = ?", parentProjectID)``, and SQL equality never matches
    NULL. A top-level project therefore takes part **only if it is stored as 0**, which is
    what upstream's create writes (measured: a project created through the API has
    ``parent_project_id`` integer 0, while all 42 top-level rows in the seed are NULL).

    So on the seed this loop legitimately renumbers *nothing*, and every project keeps the
    position it was given. This code previously matched NULL as well, with a comment
    reasoning that "both mean no parent, so matching only one silently renumbers half the
    siblings". The reasoning is sound and the conclusion is backwards: matching both is
    what renumbered the seeded projects, and it moved 12 of them on a title-only update
    that upstream leaves entirely alone. Measured on the reference server, arm by arm, in
    ``harness/probe_coder_e_position.py`` — do not "repair" this back to an ``or_``.
    """
    siblings = list(
        session.scalars(
            select(Project)
            .where(Project.parent_project_id == parent_project_id)
            .order_by(Project.position.asc())
        )
    )
    if not siblings:
        return

    max_position = float(2**32)
    for index, sibling in enumerate(siblings):
        sibling.position = max_position / len(siblings) * (index + 1)
    session.flush()


def normalize_hex(value: str | None) -> str | None:
    """Strip a leading ``#`` and keep at most six characters (``NormalizeHex``).

    Clients send ``#ff0000``; Go stores ``ff0000``. Storing the ``#`` means every colour
    round-trips differently from upstream and any comparison against stored data fails.
    """
    if value is None:
        return None
    return value.removeprefix("#")[:6]


def calculate_default_position(entity_id: int, position: float | None) -> float:
    """``tasks.go:867``. A position of zero means "unset", so derive one from the id.

    The gap of 2^16 between neighbours is what lets a drag-and-drop reorder insert
    between two rows without renumbering the rest.
    """
    if not position:
        return float(entity_id) * 65536
    return position


def check_identifier_is_free(
    session: Session, identifier: str | None, *, exclude_project_id: int | None = None
) -> None:
    """Refuse an identifier another project already holds (3007).

    Identifiers are compared uppercase, which is why they are stored that way: SQLite and
    Postgres compare case-sensitively while MySQL does not, so without normalising, the
    same filter would be unique on one database and taken on another.
    """
    if not identifier:
        return

    query = select(Project.id).where(Project.identifier == identifier.upper())
    if exclude_project_id is not None:
        query = query.where(Project.id != exclude_project_id)

    if session.scalars(query).first() is not None:
        raise CaltonError.from_name("models.ErrProjectIdentifierIsNotUnique")


#: ``FavoriteKindProject`` (``favorites.go:29-33``): the enum starts at Unknown.
FAVORITE_KIND_PROJECT = 2


def is_default_project(session: Session, project_id: int) -> bool:
    """Whether any user has this project as their default."""
    return (
        session.scalars(
            select(User.id).where(User.default_project_id == project_id).limit(1)
        ).first()
        is not None
    )


def _parent_id(project: Project) -> int:
    """A project's parent as an int, treating NULL as 0 (Go's ``parentID()``)."""
    return project.parent_project_id or 0


def check_no_cycle(session: Session, project_id: int, parent_id: int) -> None:
    """Walk up from ``parent_id``, refusing a parent chain that returns to ``project_id``.

    This is the write-side half of cycle safety (``project.go:1030-1055``). The read-side
    depth cap in :mod:`calton.permissions.project` is a backstop against hanging on
    already-corrupt data, not a substitute: it reports a broken hierarchy as *no
    permission*, which looks like an access problem rather than a data problem.
    """
    if parent_id < 0:
        # A negative id addresses a pseudo project (-1 is Favorites), which cannot own
        # anything. Returning early here instead would silently accept the value.
        raise CaltonError.from_name("models.ErrProjectCannotBelongToAPseudoParentProject")

    if parent_id == 0:
        return

    if parent_id == project_id:
        raise CaltonError.from_name("models.ErrProjectCannotBeChildOfItself")

    visited = {project_id}
    current = session.get(Project, parent_id)

    while current is not None and _parent_id(current) != 0:
        current = session.get(Project, _parent_id(current))
        if current is None:
            return
        if current.id in visited:
            raise CaltonError.from_name("models.ErrProjectCannotHaveACyclicRelationship")
        visited.add(current.id)


def create_default_buckets(session: Session, view: ProjectView, owner_id: int) -> list[Bucket]:
    """Create To-Do / Doing / Done for a manually bucketed Kanban view.

    ⚠️ **Keyed on the view, deliberately — this is not project-private.** Creating a
    *saved filter* runs the same path: upstream gives its pseudo project the same four
    views and the same three buckets under the Kanban one (measured, T29/coder-e). So does
    registering a user, via the default project. Anything that creates a view calls this;
    nothing here may start depending on a real ``projects`` row.

    The view's ``default_bucket_id`` and ``done_bucket_id`` point at the first and last,
    so they differ by two with Doing between them.

    ⚠️ **The guard is a conjunction, and neither half alone reproduces it.** Measured on
    the reference server by creating all four combinations against one project (T28) —
    the mismatched pairs are the only inputs that can tell the candidate rules apart,
    because every view the default set creates has kind and mode agreeing:

    ======  ======  ==============
    kind    mode    buckets created
    ======  ======  ==============
    kanban  manual  **3**
    kanban  none    0
    list    manual  0
    list    none    0
    ======  ======  ==============

    So "Kanban gets buckets" is wrong and "manual mode gets buckets" is wrong; both are
    required. This matters because the *read* path has a genuinely different rule — the
    board branches on **mode, not kind**, which is why seed view 974 (List kind, manual
    mode) returns buckets when you read it. Carrying that rule over to creation looks like
    consistency and would create buckets for the list+manual row above, which upstream
    does not. Two paths, two rules, and the seed contains the view that separates them.
    """
    if view.view_kind != ProjectViewKind.KANBAN:
        return []
    if view.bucket_configuration_mode != BucketConfigurationMode.MANUAL:
        return []

    buckets = []
    for title, position in DEFAULT_BUCKETS:
        bucket = Bucket(
            project_view_id=view.id,
            title=title,
            position=position,
            created_by_id=owner_id,
        )
        session.add(bucket)
        buckets.append(bucket)
    session.flush()

    view.default_bucket_id = buckets[0].id
    view.done_bucket_id = buckets[-1].id
    session.flush()
    return buckets


def create_default_views(
    session: Session,
    project_id: int,
    owner_id: int,
    *,
    with_list_filter: bool = True,
) -> list[ProjectView]:
    """Create the four views a new project always gets, in order.

    ``with_list_filter`` is upstream's ``createDefaultListFilter`` parameter
    (``CreateDefaultViewsForProject``, ``project_view.go:523``). **A saved filter passes
    False** — ``saved_filters.go:142`` — because the saved filter *is* the filter, and
    layering ``done = false`` on top of it would silently hide the user's done tasks from
    the only view that shows them. Measured on both servers: a project's default List view
    column holds the filter document, a saved filter's is NULL.

    ⚠️ **Only the List view can tell the two apart.** Gantt, Table and Kanban are NULL on
    both sides whatever this flag says, so a test written against any of them passes
    against both implementations and proves nothing.

    ⚠️ ``project_views.filter`` holds the whole marshalled ``TaskCollection``, not the
    expression — measured by reading the column out of the reference server's own database
    after creating a project through its API: ``{"s":"","sort_by":null,...,"filter":"done =
    false",...}``. Both servers agree on this in every cell except the one above, so a
    report that "Go stores the bare string" is describing the missing-filter symptom rather
    than the column format.
    """
    from calton.schemas.project_view import ViewFilter, stored_filter_of

    views = []
    for spec in DEFAULT_VIEWS:
        expression = spec.filter if with_list_filter else None
        view = ProjectView(
            project_id=project_id,
            title=spec.title,
            view_kind=spec.view_kind,
            position=spec.position,
            filter=stored_filter_of(ViewFilter(filter=expression) if expression else None),
            bucket_configuration_mode=spec.bucket_configuration_mode,
        )
        session.add(view)
        views.append(view)
    session.flush()

    # Views carry explicit positions, so this only matters if one is ever created with
    # none; applying it anyway keeps the rule in one place.
    for view in views:
        view.position = calculate_default_position(view.id, view.position)
    session.flush()

    for view in views:
        create_default_buckets(session, view, owner_id)

    return views


def create_project(
    session: Session,
    *,
    owner_id: int,
    title: str,
    description: str | None = None,
    identifier: str | None = None,
    hex_color: str | None = None,
    parent_project_id: int | None = None,
    position: float | None = None,
) -> Project:
    """Create a project and its four default views."""
    check_no_cycle(session, project_id=0, parent_id=parent_project_id or 0)
    # Order is measured, not stylistic — see each function's note. Archived answers 412
    # even to a caller with no access, and a missing parent answers 404 before permission
    # is consulted at all.
    check_parent_is_not_archived(session, parent_project_id)
    check_parent_is_writable(session, parent_project_id, owner_id)
    check_identifier_is_free(session, identifier)

    project = Project(
        owner_id=owner_id,
        title=title,
        description=description,
        # Identifiers are stored uppercase so lookups behave the same on databases that
        # compare case-sensitively and those that do not (project.go:1057-1060).
        identifier=identifier.upper() if identifier else identifier,
        hex_color=normalize_hex(hex_color),
        # 0, not NULL, for a top-level project — measured on the reference server, which
        # stores integer 0 here while every seeded top-level row is NULL. The two are
        # indistinguishable through the API (both serialise as `0`) and differ in exactly
        # one place: `recalculate_project_positions` selects by `= 0`, so a project stored
        # as NULL can never be renumbered. Writing NULL here made every API-created project
        # permanently invisible to the renumbering upstream applies to it.
        parent_project_id=parent_project_id or 0,
        position=position,
    )
    session.add(project)
    session.flush()

    # Needs the id, so it can only happen after the insert (project.go:1133).
    project.position = calculate_default_position(project.id, position)

    create_default_views(session, project.id, owner_id)
    session.flush()
    return project


#: What upstream calls the project it creates for a new account (measured).
DEFAULT_PROJECT_TITLE = "Inbox"


def create_default_project_for(session: Session, user: User) -> Project:
    """Give a newly registered user their Inbox and point ``default_project_id`` at it.

    Measured on the reference server: immediately after ``POST /register``, ``GET /user``
    reports ``settings.default_project_id`` as a **real project id**, that project is
    titled "Inbox", and it already carries the four default views (with the Kanban view's
    three buckets). Calton's registered users have ``default_project_id`` unset, so the
    frontend opens an empty default project.

    ⚠️ **This function is not wired to anything yet.** Registration lives on T14's branch
    and is not merged, so nothing calls it; ``user_service.register_user`` has to. Until
    then this is a delivered module that is not connected, which is not a delivery —
    ``test_projects_api`` carries a tripwire that fails as soon as ``/register`` appears
    in the app without a default project coming with it, so the gap cannot land quietly.

    Registration **also** creates a "My Open Tasks" saved filter, which shows up as a
    pseudo project in the new user's project list alongside this Inbox. It is created here,
    after the project, because that is the order upstream uses — visible in the allocated
    view ids, where the Inbox's four come before the filter's four — and because the pseudo
    project's ordering in ``GET /projects`` depends on it.
    """
    project = create_project(
        session,
        owner_id=user.id,
        title=DEFAULT_PROJECT_TITLE,
    )
    user.default_project_id = project.id
    session.flush()

    # Imported here, not at module scope: saved_filter_service imports create_default_views
    # from this module.
    from calton.services.saved_filter_service import create_default_filter_for

    create_default_filter_for(session, user)
    session.flush()
    return project


def _descendant_ids(session: Session, project_id: int) -> list[int]:
    """Every project below ``project_id``, breadth first."""
    found: list[int] = []
    frontier = [project_id]
    while frontier:
        children = list(
            session.scalars(select(Project.id).where(Project.parent_project_id.in_(frontier)))
        )
        # A cycle would loop forever here; the write-side check keeps one from forming,
        # and filtering what has already been seen keeps corrupt data from hanging us.
        children = [child for child in children if child not in found and child != project_id]
        if not children:
            break
        found.extend(children)
        frontier = children
    return found


def archived_project_ids(session: Session) -> Any:
    """A subquery of every project that **reads** as archived.

    ⚠️ ``is_archived`` is not just a column. Upstream reports a project as archived when
    its own flag is set **or any ancestor's is**, and the two are not the same thing:
    seed project 21 has ``is_archived = 0`` with an archived parent (22), and upstream
    answers ``true`` for it, hides it from ``GET /projects`` and refuses to create
    anything under it. Its title in upstream's own fixtures is "Test21 archived through
    parent list" — the row exists to pin exactly this.

    ⚠️ **The write-time propagation stays.** It looks redundant once this exists, and it
    is not: upstream does both — measured, archiving a parent writes ``is_archived = 1``
    onto every existing descendant, and un-archiving writes 0 back. The one sample where
    "propagate on write" and "inherit on read" would disagree is *a child created after
    its parent was archived*, and that sample **cannot be created**: upstream answers
    412/3008 to it, and so does Calton now. So the two rules coexist because the cell
    that would separate them is closed at the door — remove either one and a real case
    breaks. Arms in ``harness/probe_coder_e_archived.py``.

    Walks **down** from the archived rows rather than up from every project: the base set
    is small (a handful of archived roots) where the upward form would restate the whole
    table on every read.
    """
    archived = (
        select(Project.id.label("id"))
        .where(Project.is_archived.is_(True))
        .cte("archived_tree", recursive=True)
    )
    child = aliased(Project)
    # ⚠️ `union`, not `union_all`, and the CTE carries **only the id** — the two together
    # are what makes a parent cycle terminate. A `level` column looks like the safer
    # design (it gives you a depth bound to stop on) and is the opposite: it makes every
    # row distinct, so `union` cannot dedupe and a two-project cycle emits each id once
    # per level until the bound. Measured on a deliberate cycle: 512 copies of each id
    # with the level column, one copy each without it. With only the id, the row set is
    # bounded by the number of projects and the recursion stops when it stops growing.
    archived = archived.union(select(child.id).where(child.parent_project_id == archived.c.id))
    return select(archived.c.id)


def reads_as_archived(session: Session, project: Project) -> bool:
    """Whether this one project reports ``is_archived: true``. See above."""
    if project.is_archived:
        return True
    if not project.parent_project_id:
        return False
    return project.id in set(session.scalars(archived_project_ids(session)))


def check_parent_is_writable(session: Session, parent_project_id: int | None, user_id: int) -> None:
    """Creating **under** a parent needs ``Write`` on that parent — 404 first, then 403.

    ⚠️ **Write, not Admin, and create does not match update.** Reparenting an *existing*
    project requires Admin on both sides (CVE-2026-35595 / CVE-2026-55064), and inheriting
    that here is the obvious move. Measured on the seed's grant ladder (user 1 holds Read
    on 9, Write on 10, Admin on 11, none of them owned by them, none archived, none
    carrying a confounding team grant) — the two paths genuinely differ:

        parent held as   CREATE (PUT /projects)   UPDATE (POST /projects/{id})
        owner            201                      200
        Admin            201                      200
        Write            **201**                  **403 code 1**
        Read             403 code 0               403 code 1
        nothing          403 code 0               403 code 1
        missing          404 / 3001               404 / 3001

    Two consequences worth stating, because each is a way to get this wrong while looking
    right: the **required level differs** (Write here, Admin there), and so does the
    **403 body** — this path answers ``{"code":0,"message":"Forbidden"}`` where the
    reparent gates answer ``{"code":1,...}``. Copying either detail across makes a
    plausible implementation that diverges on a cell no test would obviously cover.
    Matrix in ``harness/probe_coder_e_create_parent_perm.py``.

    ⚠️ Ordering is measured too: a **missing** parent is 404/3001 even for a caller who
    would have been refused anyway, so existence is decided before permission — the
    opposite choice leaks nothing but reports 403 for a project that is simply not there.
    And this runs *after* :func:`check_parent_is_not_archived`, because an archived parent
    answers 412/3008 even to a caller with no access at all (measured as user2 against a
    tree they hold nothing on).

    Nothing guarded this before: any authenticated user could create a project under any
    id at all. That is not only a wrong status code — ``delete_project`` recurses into
    descendants **without re-checking permission**, so a project attached under someone
    else's tree is destroyed when they delete theirs.
    """
    if not parent_project_id:
        return
    if session.get(Project, parent_project_id) is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")
    if not can_write(session, user_id, parent_project_id):
        raise ForbiddenError()


def check_parent_is_not_archived(session: Session, parent_project_id: int | None) -> None:
    """Refuse to create a project under an archived one — 412/3008.

    ⚠️ Tested against the **inherited** flag, not the stored column, and that is measured
    rather than chosen: seed project 21 stores ``is_archived = 0`` under archived parent
    22, and upstream still answers 412 to a create beneath it
    (``harness/probe_coder_e_archived_gate.py``). Reading the column instead would let a
    client create a project inside a subtree the API itself reports as archived.

    ⚠️ This gate is also what keeps :func:`archived_project_ids` and
    :func:`set_archived_for_descendants` from contradicting each other. The one input
    where "inherit on read" and "propagate on write" disagree is a child created under an
    already-archived parent; upstream closes that input, so the disagreement has no way to
    be observed. Removing this gate does not just diverge on a status code — it reopens
    that cell and makes the two rules visibly inconsistent.

    Upstream's gate is narrower than its message suggests: renaming or deleting an
    archived project is **allowed** (both measured 200). It stops creation underneath.
    """
    if not parent_project_id:
        return
    parent = session.get(Project, parent_project_id)
    if parent is None:
        return
    if reads_as_archived(session, parent):
        raise CaltonError.from_name("models.ErrProjectIsArchived")


def set_archived_for_descendants(session: Session, project_id: int, is_archived: bool) -> None:
    """Archiving a project archives everything under it, and un-archiving frees them.

    ``setArchiveStateForProjectDescendants``. Without this a child stays writable while
    its parent is archived.
    """
    descendants = _descendant_ids(session, project_id)
    if not descendants:
        return
    session.execute(
        update(Project).where(Project.id.in_(descendants)).values(is_archived=is_archived)
    )
    session.flush()


def _check_reparent_gates(
    session: Session, *, project: Project, user_id: int, new_parent_id: int
) -> None:
    """Apply the Admin gates that guard a change of parent.

    Called only once the caller has been found to have write access, and only when the
    parent actually changes.

    ⚠️ **A new parent that does not exist is 404/3001, decided before the Admin gates.**
    Measured: moving a project to id 888888 answers ``{"code":3001}``, not the 403 the
    gates would give — and it is the *changed* branch only. Re-sending a parent id that is
    already stored and happens to be dangling must not 404, because upstream treats an
    unchanged parent as an ordinary write and never looks it up (seed project 39 is
    exactly that row: its stored parent 999999 does not exist).

    ⚠️ That unchanged case is where upstream **500s**, and we deliberately do not
    reproduce it — see the note in :func:`update_project`.
    """
    # ⚠️ `0` is "detach to the top level", not a project id — there is no row 0 to find,
    # and looking for one turns every legitimate detach into a 404. Measured: upstream
    # answers 200 for `parent_project_id: 0`.
    if new_parent_id and session.get(Project, new_parent_id) is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")
    if not is_admin(session, user_id, project.id):
        raise CaltonError.from_name("models.ErrGenericForbidden")

    # Detaching to the top level has no new parent to check.
    if new_parent_id > 0 and not is_admin(session, user_id, new_parent_id):
        raise CaltonError.from_name("models.ErrGenericForbidden")


def update_project(
    session: Session,
    *,
    project: Project,
    user_id: int,
    title: str,
    is_archived: bool = False,
    identifier: str = "",
    hex_color: str = "",
    position: float = 0.0,
    description: str | None = None,
    parent_project_id: int | None = None,
) -> Project:
    """Apply an update, writing only the whitelisted columns.

    ``parent_project_id`` of ``None`` means "not sent" — an omitted key and an explicit
    ``null`` are the same thing, because Go cannot tell them apart either. Deciding it
    from Pydantic's ``model_fields_set`` instead would read an explicit ``null`` as
    "detach to the top level" and move the project, which no Go client would ever do.
    """
    if is_archived and is_default_project(session, project.id):
        # Someone has to keep a place for new tasks to land.
        raise CaltonError.from_name("models.ErrCannotArchiveDefaultProject")

    if parent_project_id is not None and parent_project_id != _parent_id(project):
        # Upstream validates the hierarchy first and only then applies the permission
        # gates, so a cyclic move by a non-admin reports the cycle (3010/3011) rather
        # than a 403. Swapping the order changes which error the client sees.
        check_no_cycle(session, project_id=project.id, parent_id=parent_project_id)

    # ⚠️ Outside the reparent branch. Nesting it there meant a plain update could set an
    # identifier another project already held — measured 400/3007 on the reference server
    # for exactly that request, with no parent in the body at all. It stays *after* the
    # cycle check so a cyclic move still reports 3010/3011 rather than the identifier.
    check_identifier_is_free(session, identifier, exclude_project_id=project.id)

    # ⚠️ Upstream **500s** when the parent is re-sent unchanged and that stored parent is
    # dangling (seed project 39: parent 999999, which does not exist). We answer 200 and
    # keep the divergence: reproducing an unhandled server error is worse than diverging
    # from one, and the controlled answer is the better behaviour. Registered rather than
    # hidden — see corpus/_deviations.yaml `project-update-dangling-parent-500`.
    if parent_project_id is not None and parent_project_id != _parent_id(project):
        _check_reparent_gates(
            session,
            project=project,
            user_id=user_id,
            new_parent_id=parent_project_id,
        )

    # Derived from the constant rather than repeated, so the two cannot drift apart.
    # A column added to UNCONDITIONAL_UPDATE_COLUMNS without a value here fails loudly.
    unconditional = {
        "title": title,
        "is_archived": is_archived,
        # Go's zero values, not NULL: these are plain string/float fields, so an omitted
        # one is written as "" or 0 rather than nulled. The recorded contract shows
        # hex_color coming back as "" from a project that never had one.
        "identifier": identifier.upper(),
        "hex_color": normalize_hex(hex_color) or "",
        "position": position,
    }
    missing = set(UNCONDITIONAL_UPDATE_COLUMNS) - unconditional.keys()
    if missing:
        raise RuntimeError(f"no value supplied for whitelisted column(s): {sorted(missing)}")

    updates: dict[str, Any] = {
        column: unconditional[column] for column in UNCONDITIONAL_UPDATE_COLUMNS
    }

    if parent_project_id is not None:
        updates[PARENT_UPDATE_COLUMN] = parent_project_id

    # An empty description means "leave it alone", so there is no way to clear one.
    if description:
        updates[DESCRIPTION_UPDATE_COLUMN] = description

    # An omitted position is 0, which lands here and renumbers the siblings.
    #
    # ⚠️ **Before the column writes, not after** — upstream calls this at
    # ``project.go:1278``, ahead of its ``Update(colsToUpdate...)`` at 1303. The order is
    # observable rather than stylistic: the project being updated is itself one of the
    # siblings, so it is handed a renumbered position and then has it **overwritten** by
    # the position from the request. Running this afterwards leaves the renumbered value
    # in place, which is a wire difference on every title-only update — measured, upstream
    # answers ``position: 0`` where this answered ``1329394639.238``.
    #
    # The parent comes from the **request**, matching ``project.parentID()``: a nil parent
    # reads as 0, so an update that omits the parent renumbers the top level even when the
    # project itself is a child. Passing the *stored* parent instead is the natural reading
    # and picks a different set of siblings whenever the two disagree.
    if position < 0.1:
        recalculate_project_positions(session, parent_project_id or 0)

    for column, value in updates.items():
        setattr(project, column, value)

    session.flush()

    set_archived_for_descendants(session, project.id, is_archived)
    return project


def _child_project_ids(session: Session, project_id: int) -> list[int]:
    return list(session.scalars(select(Project.id).where(Project.parent_project_id == project_id)))


def delete_project(session: Session, *, project: Project, user_id: int | None = None) -> None:
    """Delete a project, its tasks, its views and every descendant.

    Tasks are removed outright, including ones already soft-deleted: there would be
    nothing to restore them into. Descendants are deleted **without** re-checking
    permissions, so a child belonging to another owner goes too.

    ``user_id`` is only consulted for the default-project rule, and only at the top of
    the call — the recursion deliberately does not pass it on, matching upstream, where
    a descendant is removed regardless of who owns it.

    ⚠️ Link shares are **not** cleaned up here: there is no model for them yet (link
    sharing is P2). Upstream deletes them alongside the grants below, so this is a known
    gap rather than a decision.
    """
    if (
        user_id is not None
        and project.owner_id != user_id
        and is_default_project(session, project.id)
    ):
        raise CaltonError.from_name("models.ErrCannotDeleteDefaultProject")

    session.execute(delete(Task).where(Task.project_id == project.id))

    # Buckets hang off the views, so they have to go before the view ids disappear.
    view_ids = list(
        session.scalars(select(ProjectView.id).where(ProjectView.project_id == project.id))
    )
    if view_ids:
        session.execute(delete(Bucket).where(Bucket.project_view_id.in_(view_ids)))
    session.execute(delete(ProjectView).where(ProjectView.project_id == project.id))

    session.execute(delete(ProjectUser).where(ProjectUser.project_id == project.id))
    session.execute(delete(TeamProject).where(TeamProject.project_id == project.id))
    session.execute(
        delete(Favorite).where(
            Favorite.entity_id == project.id, Favorite.kind == FAVORITE_KIND_PROJECT
        )
    )

    children = _child_project_ids(session, project.id)

    session.delete(project)
    session.flush()

    for child_id in children:
        child = session.get(Project, child_id)
        if child is not None:
            delete_project(session, project=child)
