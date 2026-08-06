"""Team queries and the permission questions the team endpoints ask.

Membership is the whole permission model here, and it has exactly two levels:

* **member** — may read the team. ``x-max-permission: 0``.
* **admin member** — may update it, delete it, and add/remove/promote members.
  ``x-max-permission: 2``.

There is no level 1 and no owner concept: the creator is an admin *member*, and losing
that row loses the access. Measured — ``DELETE /teams/1/members/user1`` as the only admin
succeeds, and the caller's next read of that team is 403. Nothing upstream refuses to
orphan a team, so nothing here may either.

⚠️ **A team that does not exist is 403 on the read path and 404/6002 on the write paths.**
That asymmetry is upstream's and it is the safe direction: ``GET /teams/99999`` answering
404 while ``GET /teams/1`` answers 403 would let anyone enumerate which team ids exist.
The write routes are already gated on admin membership, so by the time one of them reports
"this team does not exist" the caller has proven nothing — it is reachable only for a
missing row, never for someone else's team. Do not "make these consistent".
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError, UnauthorizedError
from calton.core.policy import ADMIN, READ
from calton.models import Team, TeamMember, User


def _team_not_found() -> CaltonError:
    return CaltonError.from_name("models.ErrTeamDoesNotExist")


def _user_not_found() -> CaltonError:
    return CaltonError.from_name("user.ErrUserDoesNotExist")


def membership(session: Session, user_id: int, team_id: int) -> TeamMember | None:
    """The caller's row in this team, or None if they are not a member."""
    return session.scalars(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    ).first()


def can_read_team(session: Session, user_id: int, team_id: int) -> tuple[bool, int]:
    """(may read, max permission). Any member may read; only an admin reports 2.

    A missing team answers ``(False, 0)`` here rather than raising, so the read path
    cannot distinguish it from someone else's team — see the module docstring.
    """
    member = membership(session, user_id, team_id)
    if member is None:
        return False, 0
    return True, ADMIN if member.admin else READ


def is_team_admin(session: Session, user_id: int, team_id: int) -> bool:
    member = membership(session, user_id, team_id)
    return member is not None and bool(member.admin)


def load_team_for_write(session: Session, team_id: int) -> Team:
    """The team, or the 404/6002 the write routes answer for a missing one.

    No permission check: the policy has already established admin membership, and a
    caller who is an admin member of a team that does not exist cannot exist either. This
    raises only for the genuinely-missing case.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise _team_not_found()
    return team


def load_user_by_name(session: Session, username: str) -> User:
    """The user with this exact name, or 404/1005.

    Upstream addresses team members **by username**, in the body of the add route and in
    the path of the other two. So ``DELETE /teams/1/members/3`` looks for a user called
    "3" and answers 1005 — which is why the path parameter must never be parsed as an id.
    """
    user = session.scalars(select(User).where(User.username == username)).first()
    if user is None:
        raise _user_not_found()
    return user


def members_of(session: Session, team_id: int) -> list[tuple[User, bool]]:
    """(user, is_admin) for every member, ordered by user id.

    ``addMoreInfoToTeams`` sorts by ``Members[i].ID``, the *user* id, so a team whose
    membership rows were created out of order still lists ascending by user.
    """
    rows = session.execute(
        select(User, TeamMember.admin)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(User.id)
    ).all()
    return [(user, bool(admin)) for user, admin in rows]


def visible_teams_query(user_id: int, search: str = "") -> Select[tuple[Team]]:
    """Teams the caller is a member of, name-searched, id ascending.

    An INNER JOIN on ``team_members``, exactly as upstream: a team is listed because you
    belong to it, never because you created it. The seed has membership rows pointing at
    team ids that were never inserted (5, 6 and 7), and the join is what keeps those from
    surfacing as rows with a null name.

    ``ILIKE %s%`` on the name, with an empty search matching everything. Upstream builds
    the same predicate unconditionally rather than skipping it when the term is empty, and
    the two only differ for a NULL name — which the column does not allow.
    """
    query = select(Team).join(TeamMember, TeamMember.team_id == Team.id)
    query = query.where(TeamMember.user_id == user_id)
    if search:
        query = query.where(Team.name.ilike(f"%{search}%"))
    return query.distinct().order_by(Team.id)


def count_visible_teams(session: Session, user_id: int, search: str = "") -> int:
    """Total matching teams, ignoring pagination.

    ⚠️ Upstream counts with a **separate query that has no DISTINCT** (teams.go:301-306),
    so a user with two membership rows in one team would be counted twice while the page
    itself lists the team once. The seed has no such duplicate, so this cannot be told
    apart from an honest count today; it is written the honest way deliberately, and if a
    corpus case ever shows the double-count this is the line to change. Copying a bug we
    cannot currently observe would be inventing evidence.
    """
    query = (
        select(func.count())
        .select_from(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
    )
    if search:
        query = query.where(Team.name.ilike(f"%{search}%"))
    return int(session.scalar(query) or 0)


def user_id_of(auth: object) -> int:
    """The authenticated subject's id, or the 401 the middleware would have sent.

    Same rule as every other resource: ``CRUDRouter`` runs the policy before anything
    else and passes ``request.state.auth`` straight through, which is ``None`` until the
    JWT or API-token dependency has populated it. ``int(None)`` would make every
    anonymous request a 500.
    """
    user_id = getattr(auth, "id", auth)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


__all__ = [
    "can_read_team",
    "count_visible_teams",
    "is_team_admin",
    "load_team_for_write",
    "load_user_by_name",
    "members_of",
    "membership",
    "user_id_of",
    "visible_teams_query",
]
