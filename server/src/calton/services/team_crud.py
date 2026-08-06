"""The team resource, wired for :class:`~calton.core.crud_router.CRUDRouter`.

Policy first, service second — so the policy's refusal is the only thing that can produce
403, and anything else has to come from the service. For teams that ordering does *not*
need the "return True for a missing object" trick labels and projects use, and the reason
is worth stating because it inverts the usual advice:

* on the **read** path a missing team must answer 403, and the policy produces that
  naturally — no member row exists, so it refuses;
* on the **write** paths a missing team must answer 404/6002, and the policy again
  refuses first... which would give 403. So the *service* never sees it.

The resolution is that the write policies here ask ``is_team_admin`` and a missing team
has no admin, so they must let the missing case through exactly the way ``LabelPolicy``
does — but only the missing case. Someone else's *existing* team stays a policy refusal
and stays 403. See :meth:`TeamPolicy.can_update`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from calton.db.base import utcnow
from calton.models import Team, TeamMember, TeamProject
from calton.schemas.team import TeamWrite
from calton.services.team_members import collapse_page_members, mark_fresh
from calton.services.team_service import (
    can_read_team,
    count_visible_teams,
    is_team_admin,
    load_team_for_write,
    user_id_of,
    visible_teams_query,
)


def _team_id(kwargs: dict[str, Any]) -> int:
    return int(kwargs.get("id", 0))


class TeamPolicy:
    """The four questions CRUDRouter asks before touching a team."""

    def can_read(self, session: Session, auth: Any, **kwargs: Any) -> tuple[bool, int]:
        """Members only, and a missing team is refused the same way.

        The second element becomes ``x-max-permission``: 2 for an admin member, 0 for a
        plain one. Measured on both, and the pair matters — a policy that always reported
        2 would still pass every status-code assertion.
        """
        return can_read_team(session, user_id_of(auth), _team_id(kwargs))

    def can_create(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Any authenticated user may create a team. The body is validated by the schema."""
        user_id_of(auth)
        return True

    def can_update(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Admin members only — except that a **missing** team is allowed through.

        ⚠️ This reads like a hole and is the only way to reproduce upstream's 404/6002 for
        ``POST /teams/99999``. CRUDRouter answers 403 the moment a policy refuses and never
        calls the service, so refusing here would turn every missing-team write into a 403
        and make the 6002 error code unreachable from this resource.

        It leaks nothing. The caller learns "no such team" only for ids that have no row at
        all; an existing team they are not an admin of is refused right here and is 403,
        indistinguishable from an id they cannot see. ``test_a_missing_team_is_404_on_write``
        and ``test_someone_elses_team_is_403_on_write`` are the pair that pins it, and
        removing this branch reddens exactly the first.

        The subject is resolved before the database is touched, so an anonymous request is
        401 rather than a query run on behalf of nobody.
        """
        user_id = user_id_of(auth)
        team_id = _team_id(kwargs)
        if session.get(Team, team_id) is None:
            return True
        return is_team_admin(session, user_id, team_id)

    def can_delete(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        return self.can_update(session, auth, **kwargs)


class TeamService:
    """The five operations, each assuming its policy has already run."""

    def create(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Team:
        """Create the team and make the creator its first, admin, member.

        The membership row is not a convenience: it is the *only* thing that gives the
        creator access afterwards. Skipping it leaves a team its author cannot read, and
        the create response would not show it — see ``TeamRead.members``, which is null
        here regardless.
        """
        body = data if isinstance(data, TeamWrite) else TeamWrite.model_validate(data)
        user_id = user_id_of(auth)
        now = utcnow()

        team = Team(
            name=body.name,
            description=body.description,
            is_public=body.is_public,
            created_by_id=user_id,
            created=now,
            updated=now,
        )
        session.add(team)
        session.flush()

        session.add(TeamMember(team_id=team.id, user_id=user_id, admin=True, created=now))
        session.flush()
        # The create response is not a view of what now exists — members are omitted and
        # created_by's timestamps are zero. See team_members.FRESH_TEAM.
        mark_fresh(session, team)
        return team

    def read_one(self, session: Session, auth: Any, **kwargs: Any) -> Team:
        """The team. Unreachable for a missing one — ``can_read`` already refused it."""
        return load_team_for_write(session, _team_id(kwargs))

    def read_all(
        self,
        session: Session,
        auth: Any,
        search: str = "",
        page: int = 1,
        per_page: int = 0,
        **kwargs: Any,
    ) -> tuple[list[Team], int, int]:
        """Teams the caller belongs to. No permission gate above this, by design.

        The count is taken before the page is sliced, so ``x-pagination-total-pages``
        describes the whole result rather than the page.
        """
        user_id = user_id_of(auth)
        query = visible_teams_query(user_id, search)
        if per_page > 0:
            query = query.limit(per_page).offset((max(page, 1) - 1) * per_page)
        teams = list(session.scalars(query))
        # The collapse is a property of the whole page, but CRUDRouter serialises one
        # item at a time — so it is computed here, where the page exists, and left in
        # Session.info for the serializer. Same carrier and same reason as FRESH_TEAM.
        collapse_page_members(session, teams)
        return teams, len(teams), count_visible_teams(session, user_id, search)

    def update(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Team:
        """Whole-model replacement — with one field that is not part of it.

        ``is_public`` is replaced: omit it and it goes back to false. ``description`` is
        guarded by a zero-value test, so an omitted *and* an explicitly empty description
        both leave the stored value alone. Those are two different rules and only the
        explicit-empty body tells them apart; see ``TeamWrite``.
        """
        team = load_team_for_write(session, _team_id(kwargs))
        body = data if isinstance(data, TeamWrite) else TeamWrite.model_validate(data)

        team.name = body.name
        if body.description != "":
            team.description = body.description
        team.is_public = body.is_public
        team.updated = utcnow()
        session.flush()
        return team

    def delete(self, session: Session, auth: Any, **kwargs: Any) -> None:
        """Delete the team and every membership row that pointed at it.

        The rows have to go explicitly: there is no foreign key on ``team_members`` to
        cascade, and orphaned rows are not inert — ``visible_teams_query`` joins through
        them, so a leftover row would make a deleted team keep appearing in its members'
        lists with whatever a later ``teams`` row of the same id happened to be.
        """
        team = load_team_for_write(session, _team_id(kwargs))
        session.query(TeamMember).filter(TeamMember.team_id == team.id).delete()
        session.query(TeamProject).filter(TeamProject.team_id == team.id).delete()
        session.delete(team)
        session.flush()
