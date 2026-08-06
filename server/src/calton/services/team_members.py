"""The three member operations, and the team serializer.

Everything here is about **the order the checks run in**, because that order is what the
client observes as a status code. Measured on the reference server, for all three routes:

    admin membership  ->  403/0     (a team that does not exist takes this branch)
    username lookup   ->  404/1005
    membership state  ->  409/6005  (add only)

The first line is the one that is easy to get wrong in the safe-looking direction. Testing
"does this team exist" first reads like better diagnostics and turns
``PUT /teams/99999/members`` into a 404 — which hands any authenticated caller a team-id
oracle. Upstream refuses first and reports nothing, and so do we.

⚠️ Note this differs from the *team* write routes, where a missing team really is
404/6002. There the caller has already been refused by the policy for every existing team
they cannot administer, so 404 is only reachable for a genuinely absent row. Here the same
refusal covers the missing case, and there is no second gate to fall through to. Two
routes on one resource, two different answers for "no such team", both measured.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.base import utcnow
from calton.db.types import ZERO_TIME
from calton.models import Team, TeamMember, User
from calton.schemas.team import TeamMemberRead, TeamRead
from calton.schemas.user import UserRead
from calton.services.team_service import (
    is_team_admin,
    load_user_by_name,
    members_of,
    membership,
)

__all__ = [
    "ZERO_TIME",
    "add_member",
    "collapse_page_members",
    "collection_team_view",
    "remove_member",
    "team_view",
    "toggle_admin",
]


def _require_team_admin(session: Session, user_id: int, team_id: int) -> None:
    """403/0 unless the caller is an admin member.

    A team that does not exist has no admin members, so it lands here — which is exactly
    the measured answer and the reason this function does not look the team up first.
    """
    if not is_team_admin(session, user_id, team_id):
        raise ForbiddenError()


def add_member(session: Session, user_id: int, *, team_id: int, username: str) -> TeamMember:
    """``PUT /teams/{id}/members``. Returns the created membership row.

    The new member is never an admin: upstream ignores an ``admin`` key in the body
    entirely, and promotion is a separate route.
    """
    _require_team_admin(session, user_id, team_id)
    user = load_user_by_name(session, username)

    if membership(session, user_id=user.id, team_id=team_id) is not None:
        raise CaltonError.from_name("models.ErrUserIsMemberOfTeam")

    member = TeamMember(team_id=team_id, user_id=user.id, admin=False, created=utcnow())
    session.add(member)
    session.flush()
    return member


def remove_member(session: Session, user_id: int, *, team_id: int, username: str) -> None:
    """``DELETE /teams/{id}/members/{username}``. Four gates, in this order.

    ⚠️ This route does **not** follow the other two, and an earlier version of this
    module got both differences wrong in the same direction — more restrictive than
    upstream, which is the direction that looks safe.

    1. **the username lookup, first — 404/1005.** It happens inside upstream's
       ``CanDelete`` (team_members_permissions.go:32), before any permission decision, so
       an unknown username answers 1005 even to a caller with no rights at all and even
       on a team where gate 3 would fire.
    2. **self-or-admin — 403/0.** ``CanDelete`` returns true when the named user *is* the
       caller: this is the "leave the team" path, and it is the only member route that is
       not admin-only (``CanCreate`` and ``CanUpdate`` are both plain ``IsAdmin``).
       Requiring admin here refuses every user's attempt to leave a team they were added
       to — a request no admin could make on their behalf either.
    3. **the last member — 400/6006.** A team whose membership is down to one row refuses
       the removal, whoever is named: measured, ``DELETE .../members/user5`` on a
       one-member team answers 6006 rather than the 200 a non-member normally gets,
       because the count is taken before anyone checks whether user5 is in the team.
    4. **the removal itself**, which is a no-op for a user who is not a member. Repeating
       the call, or naming a real non-member, is 200 — as long as gate 3 let it through.

    Note what gate 3 does *not* guard: it counts **members**, not admins. Removing the
    only admin of a two-member team succeeds and leaves a team nobody can administer —
    the survivor reads it at ``x-max-permission: 0`` and cannot add or promote anyone.
    That state is reachable upstream and is copied.
    """
    user = load_user_by_name(session, username)

    if user.id != user_id:
        _require_team_admin(session, user_id, team_id)

    if session.query(TeamMember).filter(TeamMember.team_id == team_id).count() == 1:
        raise CaltonError.from_name("models.ErrCannotDeleteLastTeamMember")

    member = membership(session, user_id=user.id, team_id=team_id)
    if member is not None:
        session.delete(member)
        session.flush()


def toggle_admin(
    session: Session, user_id: int, *, team_id: int, username: str
) -> tuple[TeamMember | None, bool]:
    """``POST /teams/{id}/members/{username}/admin``. Returns (row, new admin flag).

    ⚠️ For a user who is not a member, the row is ``None`` and the flag is ``True``, and
    **nothing is written**. Upstream flips the flag on a struct it never loaded and
    serialises it, so the client sees ``admin: true`` with a zero id and a zero timestamp.
    Returning the pair rather than raising keeps that shape reachable without the caller
    having to reconstruct it.
    """
    _require_team_admin(session, user_id, team_id)
    user = load_user_by_name(session, username)

    member = membership(session, user_id=user.id, team_id=team_id)
    if member is None:
        # No row, no write. The True is upstream's: it toggles a zero-valued struct.
        return None, True

    member.admin = not bool(member.admin)
    session.flush()
    return member, bool(member.admin)


def _user_view(user: User) -> UserRead:
    return UserRead.model_validate(user, from_attributes=True)


#: Key in ``Session.info`` naming the team this request has just created.
#:
#: ⚠️ The create response is **not** a view of the row that now exists, and the difference
#: is not cosmetic. ``PUT /teams`` answers ``"members": null`` — even though the same
#: request made the creator an admin member, which the very next ``GET`` shows — and its
#: ``created_by`` carries **zero timestamps** where every read carries the user's real
#: ones. Upstream serialises the struct it just built rather than re-reading, so the
#: response describes the request, not the database.
#:
#: CRUDRouter calls one serializer for all five operations and does not tell it which it
#: is serving, so the create path leaves a note here instead. ``Session.info`` is the
#: right carrier because the policy, the service and the serializer are guaranteed to
#: share exactly one session per request — the same guarantee ``Policy`` documents — so
#: this cannot leak between requests the way a module-level variable would.
FRESH_TEAM = "calton.teams.created_in_this_request"


def mark_fresh(session: Session, team: Team) -> None:
    """Record that ``team`` was created by this request. See :data:`FRESH_TEAM`."""
    session.info[FRESH_TEAM] = team.id


#: Key in ``Session.info`` holding the collection's collapsed membership.
#:
#: ⚠️ **``GET /teams`` loses members, deterministically, and the item route does not.**
#: Upstream collects the page's members with ``Find(&users)`` into a
#: ``map[int64]*TeamUser`` **keyed by users.id** (teams.go:154). A user who belongs to
#: several teams on the page therefore collapses to ONE entry, and lands in exactly one
#: of those teams — whichever row the query yielded last. Every other team on that page
#: reports ``members: null``.
#:
#: Measured, and predicted before it was measured: on the parity seed, user1 belongs to
#: teams 1, 2, 3, 4 and 8, and
#:
#:     ?per_page=2&page=1 -> [(1,[2]), (2,[1])]
#:     ?per_page=3&page=1 -> [(1,[2]), (2,null), (3,[1])]
#:     ?per_page=50       -> [(1,[2]), (2,null), (3,null), (4,null), (8,[1])]
#:
#: 40 repeats of the default query gave one signature, so this is a rule and not the
#: Go-map non-determinism seen elsewhere; and no member id ever appears under two teams
#: in one response. **That invariant is the discriminant for this whole implementation**
#: — an implementation that just returns everyone passes every other assertion about the
#: collection, and this is the only thing it fails.
#:
#: ``created_by`` follows from the same map: it is populated only when the creating user
#: is in it at all, which is why the seed's team 8 answers ``created_by: null`` while
#: teams 1-4 do not. Its creator is a member of no team on the page.
#:
#: This is a data-loss bug, copied deliberately (practice 17: upstream's deliberate
#: oddities are copied, its crashes are not — this is a 200 with a wrong body, not a 5xx).
#: Do not "fix" it into returning all members; ``test_a_member_appears_under_at_most_one
#: _team_per_page`` is what would notice.
COLLAPSED_MEMBERS = "calton.teams.collapsed_members_for_this_page"


def collapse_page_members(session: Session, teams: list[Team]) -> None:
    """Work out, for one page of teams, which team keeps each member.

    The winner is the member row with the **greatest team_members.id** among the page's
    teams, because that is the row upstream's unordered join yields last for that user.
    Sorting by team id instead gives the same answer on the current seed and a different
    one as soon as membership rows are created out of team order — so the mechanism is
    reproduced rather than the coincidence.
    """
    team_ids = [team.id for team in teams]
    if not team_ids:
        session.info[COLLAPSED_MEMBERS] = ({}, set())
        return

    winners: dict[int, tuple[int, int]] = {}
    rows = session.execute(
        select(TeamMember.id, TeamMember.team_id, TeamMember.user_id).where(
            TeamMember.team_id.in_(team_ids)
        )
    ).all()
    for row_id, team_id, member_user_id in rows:
        current = winners.get(member_user_id)
        if current is None or row_id > current[0]:
            winners[member_user_id] = (row_id, team_id)

    by_team: dict[int, list[int]] = {}
    for member_user_id, (_row_id, team_id) in winners.items():
        by_team.setdefault(team_id, []).append(member_user_id)

    session.info[COLLAPSED_MEMBERS] = (by_team, set(winners))


def collection_team_view(session: Session, team: Team) -> TeamRead:
    """One entry of ``GET /teams``, with the page's collapse applied.

    See :data:`COLLAPSED_MEMBERS`. Both fields here are narrower than the item route's:
    ``members`` holds only the users this team won, and ``created_by`` is null unless the
    creator is somewhere in the page's collapsed map.
    """
    by_team, in_map = session.info.get(COLLAPSED_MEMBERS, ({}, set()))
    member_ids = by_team.get(team.id)

    creator = session.get(User, team.created_by_id) if team.created_by_id in in_map else None
    members = None
    if member_ids:
        rows = session.scalars(select(User).where(User.id.in_(member_ids)).order_by(User.id))
        admin_by_user = {
            user_id: bool(admin)
            for user_id, admin in session.execute(
                select(TeamMember.user_id, TeamMember.admin).where(
                    TeamMember.team_id == team.id, TeamMember.user_id.in_(member_ids)
                )
            ).all()
        }
        members = [
            TeamMemberRead(
                id=user.id,
                name=user.name or "",
                username=user.username or "",
                admin=admin_by_user.get(user.id, False),
                created=user.created,
                updated=user.updated,
            )
            for user in rows
        ]

    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description or "",
        external_id=team.external_id or "",
        is_public=bool(team.is_public),
        created_by=_user_view(creator) if creator is not None else None,
        members=members,
        created=team.created,
        updated=team.updated,
    )


def team_view(session: Session, team: Team) -> TeamRead:
    """A team with its ``created_by`` and ``members`` resolved.

    This is the **item** shape: complete members, in user-id order. The collection's is
    :func:`collection_team_view` and is deliberately different — see
    :data:`COLLAPSED_MEMBERS`.

    ``created_by`` is ``null`` when the creating user no longer exists, rather than an
    error.
    """
    if session.info.get(FRESH_TEAM) == team.id:
        return _created_team_view(session, team)

    creator = session.get(User, team.created_by_id)
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description or "",
        external_id=team.external_id or "",
        is_public=bool(team.is_public),
        created_by=_user_view(creator) if creator is not None else None,
        members=[
            TeamMemberRead(
                id=user.id,
                name=user.name or "",
                username=user.username or "",
                admin=is_admin,
                created=user.created,
                updated=user.updated,
            )
            for user, is_admin in members_of(session, team.id)
        ],
        created=team.created,
        updated=team.updated,
    )


def _created_team_view(session: Session, team: Team) -> TeamRead:
    """The ``PUT /teams`` body: no members, and a ``created_by`` with zero timestamps.

    The creator's ``id``, ``username`` and ``name`` are real; only ``created`` and
    ``updated`` are zero, because the struct upstream echoes is the authenticated subject
    rather than the stored row. Emitting the real timestamps here is the natural thing to
    write and diverges on every single create.
    """
    creator = session.get(User, team.created_by_id)
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description or "",
        external_id=team.external_id or "",
        is_public=bool(team.is_public),
        created_by=(
            UserRead(
                id=creator.id,
                name=creator.name or "",
                username=creator.username or "",
                created=ZERO_TIME,
                updated=ZERO_TIME,
            )
            if creator is not None
            else None
        ),
        members=None,
        created=team.created,
        updated=team.updated,
    )
