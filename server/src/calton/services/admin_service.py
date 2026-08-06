"""Admin panel: overview counts, project listing/owner reassignment, user management.

Every route here is gated by ``user.is_admin`` at the handler (a 403 otherwise), so
these services do not re-check admin status — that is the router's job, in one
place. The services assume the caller is authorised.

Counts use ``select(func.count())`` rather than loading rows: the overview is a
single query per table and never needs the rows themselves.

User deletion is a **soft delete**: the row stays (so foreign keys and history
remain resolvable) and ``status`` flips to ``USER_STATUS_DISABLED``. Hard-deleting
a user upstream cascades through every table that references them, which is a
data-loss operation; the soft form is the safe default and what the design asks
for.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.models.label import Label
from calton.models.project import Project
from calton.models.task import Task
from calton.models.task_comment import TaskAttachment
from calton.models.team import Team
from calton.models.user import User
from calton.services.user_account_service import USER_STATUS_DISABLED

#: Status values the admin status-patch accepts. ``user.Status`` in the swagger
#: lists 0-3 (active, email-confirm, disabled, locked); refusing anything else
#: keeps the column honest.
VALID_STATUSES = frozenset({0, 1, 2, 3})


# --- overview ----------------------------------------------------------------


def overview(session: Session) -> dict[str, int]:
    """Row counts for the major tables. One count query each, no rows loaded."""
    return {
        "total_users": _count(session, User),
        "total_projects": _count(session, Project),
        "total_tasks": _count(session, Task),
        "total_teams": _count(session, Team),
        "total_labels": _count(session, Label),
        "total_attachments": _count(session, TaskAttachment),
    }


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


# --- projects ----------------------------------------------------------------


def list_projects(
    session: Session, *, search: str | None = None, offset: int = 0, limit: int = 0
) -> tuple[list[Project], int]:
    """All projects, optionally filtered by title/description/identifier substring."""
    stmt = select(Project)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            Project.title.ilike(like)
            | (Project.description.ilike(like))
            | (Project.identifier.ilike(like))
        )
    stmt = stmt.order_by(Project.id)
    total = _count_of(session, stmt)
    if limit > 0:
        stmt = stmt.offset(offset).limit(limit)
    rows = list(session.scalars(stmt).all())
    return rows, total


def reassign_project(session: Session, project_id: int, owner_id: int) -> Project:
    """Set a project's owner. The new owner must exist; the project must exist."""
    project = session.get(Project, project_id)
    if project is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")
    if session.get(User, owner_id) is None:
        raise CaltonError.from_name("user.ErrUserDoesNotExist")
    project.owner_id = owner_id
    session.flush()
    return project


def _count_of(session: Session, stmt: select) -> int:  # type: ignore
    """Count of a select without loading rows, ignoring any offset/limit/order.

    Re-builds the WHERE from the statement rather than wrapping it in a subquery,
    which keeps it portable across the SQLite driver and avoids the
    ``select(func.count()).select_from(stmt.subquery())`` shape that doubles the
    query's complexity for the planner.
    """
    whereclause = stmt.whereclause  # type: ignore
    count_stmt = select(func.count()).select_from(Project)
    if whereclause is not None:
        count_stmt = count_stmt.where(whereclause)
    return int(session.scalar(count_stmt) or 0)


# --- users -------------------------------------------------------------------


def list_users(
    session: Session, *, search: str | None = None, offset: int = 0, limit: int = 0
) -> tuple[list[User], int]:
    """All users, optionally filtered by username/email substring."""
    stmt = select(User)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(User.username.ilike(like) | User.email.ilike(like))
    stmt = stmt.order_by(User.id)
    total = _count_users(session, stmt)
    if limit > 0:
        stmt = stmt.offset(offset).limit(limit)
    rows = list(session.scalars(stmt).all())
    return rows, total


def _count_users(session: Session, stmt: select) -> int:  # type: ignore
    whereclause = stmt.whereclause  # type: ignore
    count_stmt = select(func.count()).select_from(User)
    if whereclause is not None:
        count_stmt = count_stmt.where(whereclause)
    return int(session.scalar(count_stmt) or 0)


def get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise CaltonError.from_name("user.ErrUserDoesNotExist")
    return user


def create_user(
    session: Session, *, username: str, email: str, password: str, name: str | None, is_admin: bool
) -> User:
    """Admin user creation.

    Reuses ``register_user`` for validation, duplication checks and the default
    project — so an admin-created account is indistinguishable from a self-registered
    one, except for the admin flag and the optional display name set afterwards.
    """
    from calton.services.user_service import register_user

    user = register_user(session, username=username, password=password, email=email)
    if name:
        user.name = name
    user.is_admin = is_admin
    session.flush()
    return user


def delete_user(session: Session, user_id: int) -> None:
    """Soft delete: flip status to disabled. The row stays."""
    user = get_user(session, user_id)
    if user.is_admin and _is_last_admin(session):
        raise CaltonError.from_name("user.ErrLastAdmin")
    user.status = USER_STATUS_DISABLED
    session.flush()


def _is_last_admin(session: Session) -> bool:
    """True if this user is the only admin left, so demoting/deleting is refused."""
    count = int(
        session.scalar(select(func.count()).select_from(User).where(User.is_admin == 1)) or 0
    )
    return count <= 1


def set_admin(session: Session, user_id: int, is_admin: bool) -> User:
    """Toggle the admin flag, refusing to demote the last remaining admin."""
    user = get_user(session, user_id)
    if user.is_admin and not is_admin and _is_last_admin(session):
        raise CaltonError.from_name("user.ErrLastAdmin")
    user.is_admin = is_admin
    session.flush()
    return user


def set_status(session: Session, user_id: int, status: int) -> User:
    """Set the account status. An invalid value is a 400, not a silent coerce."""
    if status not in VALID_STATUSES:
        raise CaltonError.from_name("models.ErrInvalidData")
    user = get_user(session, user_id)
    user.status = status
    session.flush()
    return user
