"""Team endpoints — the resource itself, plus the three that address one member.

``/teams`` and ``/teams/{id}`` go through :class:`~calton.core.crud_router.CRUDRouter`:
five of upstream's eight team routes are exactly its shape, so the inverted verbs,
``x-max-permission``, the delete body and the commit boundary are all shared rather than
respelled here.

The three member routes are hand-written, because none of them is a CRUD operation on a
team and none returns one:

* ``PUT /teams/{id}/members`` answers a **membership row** — four keys, no embedded user
* ``DELETE /teams/{id}/members/{username}`` answers the generic delete message, and does
  so even when there was nothing to delete
* ``POST /teams/{id}/members/{username}/admin`` toggles a flag and answers the membership
  row, including for a user who has no row at all

⚠️ **The member segment is a username, not an id.** ``DELETE /teams/1/members/3`` asks for
a user *named* "3" and answers 404/1005. Declaring that parameter as an int — the obvious
reading of the swagger, which even calls it ``userID`` — makes this resource answer 1005
to every real client, and no test that only sends numeric ids would ever show it.

**No policy decision is made in this module.** Which of 403 / 404-1005 / 409-6005 a
request gets is decided by the order the checks run in, and that order lives in
``services/team_members`` beside the measurements that justify it. Here we only bind it
to HTTP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.core.crud_router import CRUDRouter, deleted_response, path_param_as_id
from calton.db.session import get_db
from calton.models import Team
from calton.schemas.message import Message
from calton.schemas.team import TeamMemberCreated, TeamMemberWrite, TeamRead, TeamWrite
from calton.services import team_members
from calton.services.team_crud import TeamPolicy, TeamService
from calton.services.team_service import user_id_of

#: ``/teams`` and ``/teams/{id}``. Exposed so the app registers the registry entry from
#: the same object it mounts, which is what stops the routing table and the API-token
#: permission table from disagreeing.
TEAM_PREFIX = "/teams"
TEAM_ITEM_PARAM = "id"


def _serialize_for_crud(team: Team, session: Session, in_collection: bool) -> dict[str, object]:
    """CRUDRouter's serializer hook.

    ``created_by`` and ``members`` are both joins, so the row alone is not enough: the
    default serializer would emit ``created_by: null`` and ``members: null`` for every
    team, since neither attribute exists on the ORM class. That is a null the corpus
    catches, but it would read as a missing relationship rather than a missing serializer.

    ``in_collection`` decides which of **two different shapes** a team gets, and the
    difference is not cosmetic: the collection loses members. Upstream keys the page's
    members by user id, so a user on several of the page's teams lands in exactly one of
    them and the rest report ``members: null``. See ``team_members.COLLAPSED_MEMBERS``
    for the mechanism and the measurements. A serializer that could not tell the two
    apart would have to pick one shape, and either choice is wrong half the time.
    """
    if in_collection:
        return team_members.collection_team_view(session, team).model_dump(mode="json")
    return team_members.team_view(session, team).model_dump(mode="json")


def build_crud_router() -> CRUDRouter[Team, TeamRead, TeamWrite]:
    """The five routes ``/teams`` shares with every other CRUD resource."""
    return CRUDRouter(
        prefix=TEAM_PREFIX,
        item_param=TEAM_ITEM_PARAM,
        service=TeamService(),
        policy=TeamPolicy(),
        read_schema=TeamRead,
        write_schema=TeamWrite,
        serialize=_serialize_for_crud,
    )


def build_router() -> APIRouter:
    """The three member routes. ``/teams`` itself comes from :func:`build_crud_router`."""
    router = APIRouter()

    @router.put(
        "/teams/{id}/members",
        status_code=201,
        response_model=TeamMemberCreated,
    )
    def add_team_member(
        request: Request,
        # `str`, never `int` — an int annotation answers 422 where upstream answers
        # 400/2004. See core.crud_router.path_parameter_block.
        id: Annotated[str, Path(min_length=1)],
        body: TeamMemberWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Add one member by **username**. Admin membership required.

        Three refusals, in this order, and the order is the contract:

        1. not an admin of this team -> 403/0. **A team that does not exist takes this
           branch too**, because nobody is an admin of it — measured
           ``PUT /teams/99999/members`` -> 403, not 404.
        2. no such username -> 404/1005
        3. already a member -> 409/6005

        Checking existence before permission would turn (1) into a way to ask which team
        ids exist.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        team_id = path_param_as_id(id)

        member = team_members.add_member(session, user_id, team_id=team_id, username=body.username)
        # `get_db` closes the session without committing, so a service that only flushes
        # has its work discarded after the response was already built and sent.
        session.commit()
        return JSONResponse(
            status_code=201,
            content=TeamMemberCreated(
                id=member.id,
                username=body.username,
                admin=bool(member.admin),
                created=member.created,
            ).model_dump(mode="json"),
        )

    @router.delete("/teams/{id}/members/{username}", response_model=Message)
    def remove_team_member(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        username: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """Remove one member by username. Admin membership required.

        Answers 200 whether or not there was anything to remove: a user who was never a
        member, and the same call repeated, both measured 200 with the generic delete
        message. Only an unknown *username* is an error (404/1005).

        There is no last-admin guard, deliberately. Removing yourself as the only admin
        succeeds and leaves the team unreachable to you — measured, and copied.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        team_id = path_param_as_id(id)

        team_members.remove_member(session, user_id, team_id=team_id, username=username)
        session.commit()
        return deleted_response()

    @router.post(
        "/teams/{id}/members/{username}/admin",
        response_model=TeamMemberCreated,
    )
    def toggle_team_member_admin(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        username: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """Flip one member's admin flag. Admin membership required.

        ⚠️ For a user who is **not** a member this answers 200 with
        ``{"id": 0, "username": ..., "admin": true, "created": "0001-01-01T00:00:00Z"}``
        and writes nothing. That is upstream's behaviour, not a shortcut here: the zeroes
        are what a struct that never met a database row serialises to. It is a 200, not a
        5xx, so it is copied rather than corrected (deviation rule: copy upstream's
        deliberate oddities, do not reproduce its crashes), and
        ``test_promoting_a_non_member_is_a_200_that_writes_nothing`` is the reverse
        assertion that keeps someone from "fixing" it into a 404.

        Note also the swagger declares this route's response as ``models.Message``. It is
        not; it is the membership row. See ``contract/swagger-corrections.yaml``.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        team_id = path_param_as_id(id)

        member, admin = team_members.toggle_admin(
            session, user_id, team_id=team_id, username=username
        )
        session.commit()
        return JSONResponse(
            content=TeamMemberCreated(
                id=member.id if member is not None else 0,
                username=username,
                admin=admin,
                created=member.created if member is not None else team_members.ZERO_TIME,
            ).model_dump(mode="json")
        )

    return router


#: (method, path) for everything this module registers by hand, so route_registry and the
#: app can never disagree about which routes exist. The ``/teams`` routes are **not**
#: listed: they come from the CRUDRouter's own ``registered_actions()``, which is the
#: single source both the mount and the registry read.
#:
#: The group names these produce were checked against the reference server's own
#: ``GET /routes``: the first two file under ``teams_members`` (``create`` / ``delete``)
#: and the third under ``teams.members_admin`` — the admin route has three non-parameter
#: segments, so it is not a standard CRUD route and lands as a sub-key of its parent.
#: Getting one wrong does not break routing; it makes every API-token call against that
#: route 403 while JWT calls keep working, which reads like anything except a permissions
#: problem.
REGISTERED_ROUTES = (
    ("PUT", "/api/v1/teams/{id}/members"),
    ("DELETE", "/api/v1/teams/{id}/members/{username}"),
    ("POST", "/api/v1/teams/{id}/members/{username}/admin"),
)
