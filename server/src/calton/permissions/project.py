"""Project permissions.

The highest-risk code in Calton: getting this wrong grants access rather than failing
visibly. Two properties matter and neither is what you would write from intuition.

**Resolution has two tiers, and only the second is about distance.**

*Ownership wins at any distance.* The priority expression is
``WHEN p.owner_id = ? THEN 1 ELSE ph.level + 1``, so an owned project sorts first no
matter how far up the chain it sits. Owning a great-grandparent beats a read grant on the
immediate parent.

*Between non-ownership grants, the nearest one decides* — not the most permissive.
``ROW_NUMBER() ... ORDER BY priority`` then ``rn = 1`` picks the closest candidate, so a
project whose parent grants read and whose grandparent grants admin resolves to **read**.
Implementing this as "take the maximum over all ancestors" is a privilege escalation, and
it passes every test that only uses one level of nesting.

Stating the rule as "nearest ancestor decides" without the ownership tier is what led me
to predict two cases wrongly while writing this; the Go server corrected me.

**Permission checks compare for equality against a set, not with ``>=``.**
``checkPermission`` in ``project_permissions.go:342`` loops over the allowed values
looking for an exact match. Admin is not "at least write"; it is a member of the allowed
set for write because the set literally contains it.

The SQL is transcribed from ``project_permissions.go:383-438`` with only the placeholders
changed, deliberately not re-expressed through the ORM. It is the reference implementation
of the rule and any paraphrase is a chance to get it subtly wrong.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from enum import IntEnum

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class Permission(IntEnum):
    """``pkg/models/permissions.go``. The values are stored in the database."""

    READ = 0
    WRITE = 1
    ADMIN = 2


#: What the CTE reports for a project the user has no path to at all. Note this is *not*
#: ``Permission.READ`` — 0 means read access, so "no access" has to be a distinct value.
NO_PERMISSION = -1


class CyclicHierarchyError(RuntimeError):
    """The parent walk hit :data:`MAX_HIERARCHY_DEPTH` before reaching a root.

    Means the data is corrupt — almost certainly a cycle in ``parent_project_id``, which
    upstream rejects on write (``project.go:1040``) and Calton will too from T16.

    **For T04:** this is not a business error and must not get an error code of its own.
    It is unreachable through the supported API and cannot occur in the parity harness.
    Map it to a 500 with the standard fallback body ``{"message": "Internal Server
    Error"}`` so the response shape still honours the v1 contract; the diagnosis lives in
    the server log, which names the project and the depth.

    Deliberately not a denial: a 403 here is indistinguishable from a legitimate one, so
    data corruption would be diagnosed as a permissions problem.
    """

    def __init__(self, project_id: int, depth: int) -> None:
        self.project_id = project_id
        self.depth = depth
        super().__init__(
            f"project {project_id}: parent chain exceeded {depth} levels; "
            "permissions cannot be resolved (suspected parent_project_id cycle)"
        )


#: How far up the parent chain the walk goes before giving up.
#:
#: **This is a deliberate deviation from upstream** (approved by team-lead; registered in
#: the design doc's deviation list). Go's query has no bound, so a cycle in
#: ``parent_project_id`` makes it recurse forever — I verified both implementations hang.
#: Upstream's only defence is at write time (``project.go:1040`` rejects a reparent that
#: would create a cycle), so a cycle arriving by any other route — a direct write, an
#: import, a bug in some other path that sets ``parent_project_id`` — turns every
#: permission check touching that subtree into a stuck request, unbounded.
#:
#: Under a synchronous threadpool a hung query is worse than slow: it holds a worker and a
#: connection and never returns either, so a few dozen requests touching the poisoned
#: subtree exhaust the pool and take down endpoints unrelated to the cycle. Go gets a
#: goroutine per request and degrades gradually.
#:
#: The bound only changes behaviour for inputs that are either cyclic or absurd, and those
#: are exactly the inputs where Go hangs rather than answering, so the parity harness can
#: never observe a difference. Hanging alongside Go is not a behaviour worth preserving.
#:
#: 512 is far past anything a real hierarchy reaches while staying cheap to walk.
MAX_HIERARCHY_DEPTH = 512

#: From ``project_permissions.go:383-438``. The edits are ``?`` to named parameters, the
#: inlined id list to an expanding bind parameter, and the depth bound described above
#: (the ``level`` condition on the recursive term and ``MAX(ph.level)`` in the output, so
#: the caller can tell a truncated walk from a complete one).
_MAX_PERMISSIONS_SQL = text("""
WITH RECURSIVE
    project_hierarchy AS (
        -- Base case: Start with the specified projects
        SELECT id,
               parent_project_id,
               0  AS level,
               id AS original_project_id
        FROM projects
        WHERE id IN :project_ids

        UNION ALL

        -- Recursive case: Traverse up the hierarchy
        SELECT p.id,
               p.parent_project_id,
               ph.level + 1,
               ph.original_project_id
        FROM projects p
                 INNER JOIN project_hierarchy ph ON p.id = ph.parent_project_id
        WHERE ph.level < :max_depth),

    -- Calculate max team permission for each project/user combination
    max_team_permissions AS (
        SELECT tl.project_id,
               MAX(tl.permission) AS max_team_permission
        FROM team_projects tl
                 INNER JOIN team_members tm ON tm.team_id = tl.team_id AND tm.user_id = :user_id
        GROUP BY tl.project_id
    ),

    project_permissions AS (SELECT ph.id,
                                   ph.original_project_id,
                                   CASE
                                       WHEN p.owner_id = :user_id THEN 2
                                       WHEN COALESCE(ul.permission, 0) > COALESCE(mtp.max_team_permission, 0) THEN ul.permission
                                       ELSE COALESCE(mtp.max_team_permission, 0)
                                       END AS project_permission,
            CASE
                WHEN p.owner_id = :user_id THEN 1  -- Direct project ownership
                ELSE ph.level + 1  -- Derived from parent project
            END AS priority
                            FROM project_hierarchy ph
                                LEFT JOIN projects p
                            ON ph.id = p.id
                                LEFT JOIN users_projects ul ON ul.project_id = ph.id AND ul.user_id = :user_id
                                LEFT JOIN max_team_permissions mtp ON mtp.project_id = ph.id
                            WHERE p.owner_id = :user_id OR ul.user_id = :user_id OR mtp.max_team_permission IS NOT NULL)

-- Upstream ambiguity, deliberately preserved: ORDER BY priority has no tiebreaker, and
-- two rows can share priority 1 — an owned ancestor, and a direct grant on the project
-- itself (level 0 + 1). Which one ROW_NUMBER picks is whatever order the engine
-- produces. SQLite happens to pick the direct grant, and Go running the same query on
-- SQLite agrees, but another engine may not. Do not add a tiebreaker: that would be a
-- deviation, and it would change answers Go does not change.
SELECT ph.original_project_id AS id,
       COALESCE(MAX(pp.project_permission), -1) AS max_permission,
       MAX(ph.level) AS depth
FROM project_hierarchy ph
         LEFT JOIN (SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY original_project_id ORDER BY priority) AS rn
                    FROM project_permissions) pp ON ph.id = pp.id AND pp.rn = 1
GROUP BY ph.original_project_id
""").bindparams(bindparam("project_ids", expanding=True))


def max_permissions_for_projects(
    session: Session, user_id: int, project_ids: Sequence[int]
) -> dict[int, int]:
    """Resolve each project to the permission this user holds on it.

    A project that exists but grants the user nothing maps to :data:`NO_PERMISSION`.
    A project that does not exist is **absent from the result** — the two cases are
    distinct upstream and callers rely on the difference.

    Raises :class:`CyclicHierarchyError` if the parent walk reaches
    :data:`MAX_HIERARCHY_DEPTH`, which only corrupt data can cause.
    """
    if not project_ids:
        return {}

    # Pseudo ids (-1 Favorites, < -1 saved filters) have no row and are handled before
    # this point upstream — checkPermissionsForProjects only ever receives ids that
    # exist. Letting one through here would return "absent", which callers read as a
    # denial, hiding the routing bug instead of reporting it.
    pseudo = [project_id for project_id in project_ids if project_id < 0]
    if pseudo:
        raise ValueError(
            f"pseudo project ids reached the permission query: {pseudo}. "
            "Route through calton.permissions.pseudo.resolve() first."
        )

    rows = session.execute(
        _MAX_PERMISSIONS_SQL,
        {
            "user_id": user_id,
            "project_ids": list(project_ids),
            "max_depth": MAX_HIERARCHY_DEPTH,
        },
    )

    resolved = {}
    for row in rows:
        if row.depth >= MAX_HIERARCHY_DEPTH:
            # The walk stopped at the bound, so an ancestor carrying a stronger grant may
            # not have been seen. Neither answer available here is safe to return:
            # reporting the partial result can drop the owner row and silently lose admin,
            # and denying produces a 403 indistinguishable from a legitimate one, which
            # gets the data corruption diagnosed as a permissions problem instead.
            # Raising is the only outcome that cannot be mistaken for a permission
            # decision. Same reasoning as the pseudo-id guard above.
            logger.error(
                "project %s: parent chain reached the depth limit of %s while resolving "
                "permissions for user %s. Suspected cycle in parent_project_id, or a "
                "hierarchy deeper than the limit. Permissions cannot be resolved for "
                "this project until the data is repaired.",
                row.id,
                MAX_HIERARCHY_DEPTH,
                user_id,
            )
            raise CyclicHierarchyError(project_id=row.id, depth=row.depth)

        resolved[row.id] = row.max_permission

    return resolved


def max_permission(session: Session, user_id: int, project_id: int) -> int | None:
    """The permission held on one project, or None when the project does not exist."""
    return max_permissions_for_projects(session, user_id, [project_id]).get(project_id)


def check_permission(
    session: Session, user_id: int, project_id: int, allowed: Iterable[Permission]
) -> bool:
    """Whether the user's permission is **exactly one of** ``allowed``.

    Equality, not ordering — mirroring ``checkPermission``. Callers pass the full set they
    accept (write means ``{WRITE, ADMIN}``), so writing this as ``>=`` would happen to
    agree today and diverge the moment a new permission value is introduced between them.
    """
    held = max_permission(session, user_id, project_id)
    if held is None:
        return False

    return any(held == permission for permission in allowed)


def can_read(session: Session, user_id: int, project_id: int) -> tuple[bool, int]:
    """``(can_read, max_permission)`` — the second value populates ``x-max-permission``.

    Returns ``0`` rather than ``-1`` when access is denied, matching Go: its switch simply
    does not match ``-1``, leaving the struct field at its zero value.
    """
    held = max_permission(session, user_id, project_id)
    if held is None or held not in tuple(Permission):
        return False, 0

    return True, held


def can_write(session: Session, user_id: int, project_id: int) -> bool:
    return check_permission(session, user_id, project_id, (Permission.WRITE, Permission.ADMIN))


def is_admin(session: Session, user_id: int, project_id: int) -> bool:
    return check_permission(session, user_id, project_id, (Permission.ADMIN,))


#: Deleting a project requires admin, same as any other administrative change.
can_delete = is_admin
