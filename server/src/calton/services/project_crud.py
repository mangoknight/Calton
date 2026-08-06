"""The project resource, wired for :class:`~calton.core.crud_router.CRUDRouter`.

⚠️ **``can_read``, ``can_update`` and ``can_delete`` all answer True for a project that
does not exist, and that is not a hole.** Read this before "tightening" any of them.

CRUDRouter runs policy first and service second, and a policy refusal becomes 403 without
the service ever running. So a policy that refuses missing objects can only ever produce
403 for them — while the reference server answers **404 / 3001**, measured on every one of
``GET``, ``POST`` and ``DELETE /projects/999999``. The only way to reach 404 through this
pipeline is for the policy to decline to judge and let :func:`load_project` raise. A
project that *exists* but belongs to someone else is still refused by the policy and is
still a 403 — the two cases stay distinct, they are just decided in different layers.

Closing this "hole" would turn **every 404 on this resource into a 403**, silently, and
the diff would look like nothing but added strictness.

⚠️ The read path here differs from labels. ``labels`` reports 403 for missing *and*
invisible so that ids cannot be enumerated; projects report 404 for missing and 403 for
invisible, which does disclose existence. Both are measured. Do not generalise either one
onto the other.

**``can_update`` is based on write, not admin** (``CanUpdate`` ends in ``p.CanWrite``).
Making it admin-only would reject every ordinary edit by a write-permission collaborator,
which no test using only an owner would notice. The reparent Admin gates live in
:func:`~calton.services.project_service.update_project`, deliberately below this layer:
they depend on *what changed*, which a policy that only sees the id cannot know.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError, UnauthorizedError
from calton.db.base import utcnow
from calton.models.project import Project
from calton.models.saved_filter import SavedFilter
from calton.models.team import ProjectUser, TeamMember, TeamProject
from calton.models.user import User
from calton.permissions import project as project_permissions
from calton.permissions.pseudo import (
    FAVORITES_PSEUDO_PROJECT_ID,
    Favorites,
    RealProject,
    SavedFilterProject,
    project_id_from_saved_filter_id,
    resolve,
)
from calton.schemas.project import ProjectWrite
from calton.services import project_service, saved_filter_service
from calton.services.project_view_service import favorites_views


def _user_id(auth: Any) -> int:
    """The caller's id, or the middleware's 401.

    ⚠️ Not a convenience. ``CRUDRouter.read_all`` deliberately runs **no permission
    gate** — DoReadAll has none upstream either — so on an unauthenticated request the
    subject reaching this layer is ``None`` and nothing above has objected yet. Coercing
    it (``int(None)``) raises inside the service and the caller sees a 500 where the
    reference server answers ``401 {"code": 11}``; schemathesis found exactly that on
    ``GET /projects``. Failing closed here is what makes the collection route behave like
    the item routes while the auth middleware (T14/T15) is still unmerged.
    """
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def load_project(session: Session, project_id: int) -> Project:
    """The project, or the 404 the reference server gives for a missing one."""
    project = session.get(Project, project_id)
    if project is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")
    return project


def visible_projects_query(session: Session, user_id: int) -> Select[tuple[Project]]:
    """Every project the user can see, by ownership, direct grant or team grant.

    ⚠️ **Visibility descends the parent chain**: owning a project makes every project
    *under* it visible, including ones owned by somebody else. Measured — alice owns 904,
    bob owns 905 whose parent is 904, and ``GET /projects`` as alice lists 905. Writing
    this as "owner or has a grant" is the intuitive reading and drops those rows; it also
    does not fail the obvious tests, because a fixture where every child shares its
    parent's owner cannot tell the two rules apart.

    The descent is expressed as "some ancestor is visible", which is the same relation the
    permission CTE walks upwards. It does **not** descend for grants the other way: bob
    holds nothing on 904 and does not see it, so this is strictly a downward rule.
    """
    directly_visible = (
        select(Project.id)
        .outerjoin(ProjectUser, ProjectUser.project_id == Project.id)
        .outerjoin(TeamProject, TeamProject.project_id == Project.id)
        .outerjoin(
            TeamMember,
            (TeamMember.team_id == TeamProject.team_id) & (TeamMember.user_id == user_id),
        )
        .where(
            or_(
                Project.owner_id == user_id,
                (ProjectUser.user_id == user_id),
                (TeamMember.user_id == user_id),
            )
        )
    )

    # Walk down from everything visible directly. Bounded by the same depth cap the
    # permission query uses, so corrupt data cannot make this loop forever.
    reachable = set(session.scalars(directly_visible))
    frontier = set(reachable)
    for _ in range(project_permissions.MAX_HIERARCHY_DEPTH):
        if not frontier:
            break
        children = set(
            session.scalars(select(Project.id).where(Project.parent_project_id.in_(frontier)))
        )
        frontier = children - reachable
        reachable |= frontier

    return select(Project).where(Project.id.in_(reachable))


#: ``project.go:156-191``. Every field of the Favorites pseudo project is a constant on a
#: package-level var; nothing is read from the ``favorites`` table. That table is what
#: ``GET /projects/-1/tasks`` needs — the project *body* needs none of it, which is why
#: this can be served before favourites themselves exist.
FAVORITES_TITLE = "Favorites"
FAVORITES_DESCRIPTION = "This project has all tasks marked as favorites."
#: Sorts it to the top of any list ordered by position.
FAVORITES_POSITION = -1.0
#: ``project_permissions.go:118-126`` hands back ``PermissionRead``, **not** Admin. A saved
#: filter's pseudo project answers 2 on the same header; these two pseudo projects
#: disagree, and reusing one constant for both is a one-character way to get it wrong.
FAVORITES_MAX_PERMISSION = 0

#: ⚠️ ``created``/``updated`` on Favorites are the **process start time**, not the request
#: time. Upstream writes ``Created: time.Now()`` in a package-level ``var`` initialiser
#: (``project.go:189-190``), so it is evaluated once when the binary starts and every
#: response for the rest of that process's life carries the same instant. Measured: two
#: requests 1.1 s apart returned byte-identical timestamps.
#:
#: Captured the same way here rather than per request, because "constant for the life of
#: the process" is the observable behaviour — a client polling this endpoint sees a project
#: whose creation date never moves. The absolute value is necessarily different from Go's
#: (different process), so the parity harness compares it as ``<TS>``; what is reproduced
#: is that it is a valid timestamp and that it does not change between requests.
FAVORITES_TIMESTAMP = utcnow()

# ⚠️ The three Favorites views used to be spelled out again here, as tuples whose filter
# was the bare expression ``"done = false"``. That second copy is what
# ``project_views.filter`` **does not** hold — the column stores the whole marshalled
# TaskCollection — so once the project body started rendering through the same serializer
# as the view endpoints, this table's rows parsed to ``filter: null`` and the Favorites
# List view silently lost its filter. Two tables of the same constants drifted the moment
# anything downstream of them changed.
#
# There is now one table, in ``project_view_service``, shared by the view endpoints and by
# the project body. It is the same three views either way; they must not be able to
# disagree.


#: Instance attribute marking a project whose ``owner`` upstream fills from **the
#: authenticated subject** rather than by reading the users row. Only those paths show
#: Go's zero time, and only under a JWT — see ``auth.deps.AuthSubject`` for the measured
#: 2x2. Carried on the object because ``CRUDRouter`` hands the serialiser a project and a
#: session and nothing else; the alternative is a signature change on the base every
#: resource shares.
SUBJECT_OWNER_ATTR = "owner_timestamps_are_zero"

#: Instance attribute holding a precomputed "does this read as archived" answer, so a page
#: of projects costs one query instead of one per project. Purely an optimisation — the
#: serializer recomputes when it is absent, so a path that does not set it is slower, never
#: wrong. See ``api.v1.projects._reads_as_archived``.
ARCHIVED_ATTR = "reads_as_archived"


def _mark_archived(session: Session, projects: list[Project]) -> None:
    """Precompute the inherited ``is_archived`` for a whole page in one query."""
    if not projects:
        return
    archived = set(session.scalars(project_service.archived_project_ids(session)))
    for project in projects:
        setattr(project, ARCHIVED_ATTR, project.id in archived or bool(project.is_archived))


def _mark_subject_owner(project: Project, auth: Any) -> Project:
    """Record that this project's ``owner`` came from the subject, and how it will read.

    Applied at the three sites upstream assigns ``project.Owner = a.(*user.User)``:
    Favorites, a saved filter's pseudo project, and the create response. Every other path
    re-reads the row and is unaffected.
    """
    setattr(project, SUBJECT_OWNER_ATTR, bool(getattr(auth, "timestamps_are_zero", False)))
    return project


def favorites_project(user_id: int) -> Project:
    """The synthetic Favorites project, id ``-1``.

    Transient and never added to the session — there is no row and flushing one would
    create a project called "Favorites" owned by whoever asked first.

    ``owner`` is the **caller**: every user sees themselves as the owner of their own
    Favorites, so this is per-request rather than a constant on the object above.

    ⚠️ One measured difference is deliberately *not* reproduced. Upstream fills ``Owner``
    from the authenticated subject rather than from the users table, so the embedded
    ``created``/``updated`` come back as the **zero time** when the caller authenticated
    with a JWT, and as the real values when they used an API token. That is a property of
    Go's two auth mechanisms, not of this endpoint, and copying it here would spread an
    auth-layer artefact into project serialisation. Calton reads the row, which matches the
    API-token case the corpus exercises. If a JWT-authenticated parity case is ever added
    for this path, this is the line it will fail on, and this note is why.
    """
    project = Project(
        id=FAVORITES_PSEUDO_PROJECT_ID,
        title=FAVORITES_TITLE,
        description=FAVORITES_DESCRIPTION,
        owner_id=user_id,
        parent_project_id=0,
        position=FAVORITES_POSITION,
        created=FAVORITES_TIMESTAMP,
        updated=FAVORITES_TIMESTAMP,
    )
    project.is_favorite = True  # type: ignore[attr-defined]
    # Attached rather than looked up: `_sorted_views` queries `project_views` by
    # project_id, and there are no rows for -1. See api/v1/projects._sorted_views.
    # Attached rather than looked up: `_sorted_views` queries `project_views` by
    # project_id, and there are no rows for -1. See api/v1/projects._sorted_views.
    project.synthetic_views = favorites_views()  # type: ignore[attr-defined]
    return project


def pseudo_project_from_filter(saved: SavedFilter) -> Project:
    """One saved filter, dressed as a project — ``SavedFilter.ToProject``.

    Transient, never added to the session: a saved filter has no row in ``projects`` and
    flushing one would create it.

    ``is_favorite`` is set here rather than defaulted by the serializer because it is the
    one field of a pseudo project that is genuinely per-row. It is *not* a mapped column —
    ``projects`` has no such column, favourites being their own table — so it lives in the
    instance dict and the serializer reads it with ``getattr``. The same object serves the
    collection and the item, which is what stops the two from disagreeing about a filter
    the user has favourited.

    ``ToProject`` copies six fields and no more: id, title, description, is_favorite,
    created, updated and owner. Everything else — identifier, hex_color, position,
    is_archived — stays at its zero value, so a saved filter always presents as an
    unarchived, uncoloured project at position 0.
    """
    project = Project(
        id=project_id_from_saved_filter_id(saved.id),
        title=saved.title,
        description=saved.description or "",
        owner_id=saved.owner_id,
        parent_project_id=0,
        position=0,
        created=saved.created,
        updated=saved.updated,
    )
    project.is_favorite = bool(saved.is_favorite)  # type: ignore[attr-defined]
    return project


def saved_filter_pseudo_projects(session: Session, user_id: int) -> list[Project]:
    """The user's saved filters, dressed as projects.

    ⚠️ These are **appended after pagination, on every page**. Measured: with
    ``per_page=5`` the body holds six entries on page 1, six on page 2 and — on a page
    past the end — exactly one, the pseudo project, with ``x-pagination-result-count: 0``.
    So the header counts real projects only and the body carries one more than it says.
    Reproducing that means the count must be taken before these are added, not after.
    """
    return [
        pseudo_project_from_filter(saved)
        for saved in saved_filter_service.filters_owned_by(session, user_id)
    ]


class ProjectPolicy:
    """Answers the four questions CRUDRouter asks. See the module docstring first."""

    def can_read(self, session: Session, auth: Any, **kwargs: Any) -> tuple[bool, int]:
        """403 for a project the caller cannot see; **True for one that does not exist**.

        Letting the missing case through is what produces 404/3001 from the service. It is
        the opposite of the labels policy, and both are measured.
        """
        # ⚠️ Resolved first, before anything touches the database. The policy is the
        # outermost layer CRUDRouter runs, so if it queries before establishing who is
        # asking, an unauthenticated request reaches the database and fails there —
        # schemathesis found exactly that, as a 500 on four project routes, where
        # upstream answers 401. Identity first, then rows.
        user_id = _user_id(auth)
        project_id = int(kwargs.get("project", 0))
        pseudo = resolve(project_id)

        if isinstance(pseudo, SavedFilterProject):
            # Delegated wholesale to the filter's own permission check
            # (project_permissions.go:127-135 -> saved_filters_permissions.go:52-66), which
            # is why all three of these are decided here rather than by the service:
            #
            #   missing filter -> 404/11001, raised *by the permission check*
            #   someone else's -> 403 "You don't have the permission to see this"
            #   yours          -> 200, x-max-permission 2
            #
            # The 404 has to be raised, not returned as False: CRUDRouter turns a False
            # into 403, and the reference server distinguishes the two — a saved filter
            # discloses which ids exist, unlike labels, where both are an identical 403.
            saved_filter_service.load_for_read(session, user_id, pseudo.filter_id)
            # CanRead answers PermissionAdmin regardless of caller; by this point the
            # caller is known to be the owner, so there is nothing else it could be.
            return True, saved_filter_service.SAVED_FILTER_MAX_PERMISSION

        if isinstance(pseudo, Favorites):
            # Readable by anyone authenticated: there is nothing to own. The permission
            # is Read (0), *not* the Admin (2) a saved filter's pseudo project reports.
            return True, FAVORITES_MAX_PERMISSION
        if session.get(Project, project_id) is None:
            return True, 0
        return project_permissions.can_read(session, user_id, project_id)

    def can_create(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Any *authenticated* user may create a project — the qualifier is the check."""
        _user_id(auth)
        return True

    def can_update(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Write, not admin — see the module docstring. True for a missing project."""
        user_id = _user_id(auth)
        project_id = int(kwargs.get("project", 0))
        if not isinstance(resolve(project_id), RealProject):
            # Measured: POST /projects/-1 is 403 code 0, not a 400 or a 404.
            return False
        if session.get(Project, project_id) is None:
            return True
        return project_permissions.can_write(session, user_id, project_id)

    def can_delete(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Admin. A write-permission collaborator is refused (measured: 403 code 0)."""
        user_id = _user_id(auth)
        project_id = int(kwargs.get("project", 0))
        if not isinstance(resolve(project_id), RealProject):
            return False
        if session.get(Project, project_id) is None:
            return True
        return project_permissions.is_admin(session, user_id, project_id)


class ProjectService:
    """The five operations. Each assumes its policy has already run."""

    def create(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Project:
        # ⚠️ The write methods here commit; the functions in ``project_service`` only
        # flush. That split is deliberate: those functions are also driven directly by
        # unit tests that own their transaction, and ``get_db`` closes the request
        # session without committing. Leaving the commit out means every write answers
        # 200 with a fully populated body and persists nothing — the response is built
        # from the flushed objects, so nothing looks wrong until the next request.
        body = data if isinstance(data, ProjectWrite) else ProjectWrite.model_validate(data)
        # ⚠️ A body `id` on **create** is not used to create anything — but it is looked up
        # first, and a miss is a 404. `CreateProject` (project.go) opens with
        # `project.CheckIsArchived(s)`, which loads the row by `project.ID`, and only
        # *afterwards* does `project.ID = 0` and insert. So the id the client sent is
        # discarded for the insert while still deciding whether the request survives.
        #
        # Measured, and the pair is what makes it unambiguous:
        #     PUT /projects {"title": "x", "id": 99999}  -> 404/3001
        #     PUT /projects {"title": "x", "id": 906}    -> 201, a NEW project; 906 untouched
        # Without the second arm this reads as "a body id updates that project", which is
        # the natural guess and is wrong.
        if body.id:
            load_project(session, body.id)

        created = project_service.create_project(
            session,
            owner_id=_user_id(auth),
            title=body.title,
            description=body.description,
            identifier=body.identifier,
            hex_color=body.hex_color,
            parent_project_id=body.parent_project_id,
            position=body.position,
        )
        session.commit()
        # The create response embeds the subject as owner; a read of the same project
        # afterwards does not. Measured: PUT answers the zero time under a JWT, the
        # following GET answers the real one.
        return _mark_subject_owner(created, auth)

    def read_one(self, session: Session, auth: Any, **kwargs: Any) -> Project:
        project_id = int(kwargs.get("project", 0))
        pseudo = resolve(project_id)
        if isinstance(pseudo, Favorites):
            return _mark_subject_owner(favorites_project(_user_id(auth)), auth)
        if isinstance(pseudo, SavedFilterProject):
            # The policy above has already established that this filter exists and is
            # ours, so this load cannot fail — it is repeated rather than threaded through
            # because CRUDRouter gives the service no channel to the policy's result, and a
            # cached one would be a second place for the ownership answer to live.
            saved = saved_filter_service.load_for_read(session, _user_id(auth), pseudo.filter_id)
            return _mark_subject_owner(pseudo_project_from_filter(saved), auth)
        return load_project(session, project_id)

    def read_all(
        self,
        session: Session,
        auth: Any,
        search: str,
        page: int,
        per_page: int,
        is_archived: bool = False,
        **kwargs: Any,
    ) -> tuple[list[Project], int, int]:
        """Visible projects for this page, plus the saved-filter pseudo projects.

        ``is_archived`` is upstream's spelling of *include* archived, not *only* archived:
        the default hides them and ``?is_archived=true`` shows them alongside the rest.
        Reading it as a filter for archived-only would make the parameter return a strict
        subset instead of a superset.
        """
        query = visible_projects_query(session, _user_id(auth))
        if not is_archived:
            # ⚠️ Excluded by the *inherited* flag, not the column. A project whose own
            # flag is 0 but whose parent is archived is hidden by upstream too, and doing
            # this in Python after the LIMIT would drop rows out of an already-paginated
            # page — the count and the body would disagree.
            query = query.where(Project.id.not_in(project_service.archived_project_ids(session)))
        if search:
            query = query.where(Project.title.icontains(search))

        total_items = len(list(session.scalars(query)))
        page_query = query.order_by(Project.position.asc(), Project.id.asc())
        if per_page > 0:
            page_query = page_query.limit(per_page).offset(max(page - 1, 0) * per_page)
        items = list(session.scalars(page_query))
        result_count = len(items)

        # Counted before the pseudo projects join the body — see the note on
        # saved_filter_pseudo_projects for why the two numbers disagree on purpose.
        _mark_archived(session, items)
        return (
            items
            + [
                _mark_subject_owner(pseudo, auth)
                for pseudo in saved_filter_pseudo_projects(session, _user_id(auth))
            ],
            (result_count),
            total_items,
        )

    def update(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Project:
        body = data if isinstance(data, ProjectWrite) else ProjectWrite.model_validate(data)
        project = load_project(session, int(kwargs.get("project", 0)))
        updated = project_service.update_project(
            session,
            project=project,
            user_id=_user_id(auth),
            title=body.title,
            is_archived=body.is_archived,
            identifier=body.identifier,
            hex_color=body.hex_color,
            position=body.position,
            description=body.description,
            parent_project_id=body.parent_project_id,
        )
        session.commit()
        return updated

    def delete(self, session: Session, auth: Any, **kwargs: Any) -> None:
        project = load_project(session, int(kwargs.get("project", 0)))
        project_service.delete_project(session, project=project, user_id=_user_id(auth))
        session.commit()


def users_with_access(session: Session, project_id: int) -> list[User]:
    """Everyone who can reach this project: the owner, direct grants, team members.

    Ordered by id, and asserted as an exact list rather than a count — a rule that
    over-counts (every team member of every team) or under-counts (owner omitted) returns
    the right length surprisingly often.
    """
    project = load_project(session, project_id)

    user_ids = {project.owner_id}
    user_ids |= set(
        session.scalars(select(ProjectUser.user_id).where(ProjectUser.project_id == project_id))
    )
    user_ids |= set(
        session.scalars(
            select(TeamMember.user_id)
            .join(TeamProject, TeamProject.team_id == TeamMember.team_id)
            .where(TeamProject.project_id == project_id)
        )
    )

    return list(session.scalars(select(User).where(User.id.in_(user_ids)).order_by(User.id.asc())))
