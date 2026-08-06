"""Team wire shapes.

Two resources share this module and they disagree about what ``id`` means, which is the
single most important thing on this page:

* inside a team, ``members[].id`` is the **user** id — the embedded object is a user with
  an ``admin`` flag bolted on;
* the body ``PUT /teams/{id}/members`` answers with is a **membership row**, and its
  ``id`` is the ``team_members`` primary key.

Measured on the reference server: adding ``user4`` (user id 4) to team 1 answers
``{"id": 57, "username": "user4", "admin": false, "created": ...}`` while the same team's
``GET`` lists that member as ``{"id": 4, "username": "user4", ...}``. Both are called
``id`` and both are ints, so nothing but this note and the two separate classes stops a
later reader from "unifying" them — after which the member endpoint would answer a user
id and no test that only checks types would notice.

Field names follow upstream, not the column names: the JSON is ``is_public`` and
``external_id`` where the Go struct fields are ``IsPublic`` and ``ExternalID``, and there
is no ``public``/``oidc_id`` spelling anywhere on the wire.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field

from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class TeamMemberRead(CaltonModel):
    """A member *inside* a team: the embedded user, plus ``admin``.

    ``id`` is the user id here. See the module docstring for the other ``id``.

    ``email`` is absent for the same reason it is absent from every embedded user: a team
    is readable by all of its members, so carrying the address would publish it to
    everyone the team touches.
    """

    id: int
    name: str = ""
    username: str = ""
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME
    admin: bool = False


class TeamRead(CaltonModel):
    """A team, as ``GET``/``PUT``/``POST /teams`` return it.

    ``members`` is ``null``, not ``[]``, when it was never populated — and that is a real
    distinction rather than a serialisation accident. ``PUT /teams`` answers
    ``"members": null`` even though the creator *is* made an admin member by the same
    request; the subsequent ``GET`` shows them. So the create response is not a view of
    the row that now exists, and hydrating it here would be an improvement no client
    asked for.

    ``created_by`` is likewise not always the stored row. On create it is the
    authenticated subject echoed back with **zero timestamps**; on read it is the user
    loaded from the database with real ones.
    """

    id: int
    name: str = ""
    description: str = ""
    external_id: str = ""
    created_by: UserRead | None = None
    members: list[TeamMemberRead] | None = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME
    is_public: bool = False


class TeamWrite(CaltonModel):
    """The body of ``PUT /teams`` and ``POST /teams/{id}``.

    ``name`` is required in upstream's sense — non-zero, not non-blank. An empty string
    and an absent key are both ``412/2002`` with
    ``invalid_fields: ["name: non zero value required"]``, measured on both the create and
    the update route, so nothing here may ``strip()`` before testing emptiness and a
    whitespace-only name must be accepted.

    ⚠️ **``description`` is not part of the full replace, and the discriminating case is
    not the one you would reach for.** Sending ``{"name": "x"}`` alone leaves the stored
    description untouched, which reads like "description is exempt". It is not: sending
    ``{"name": "x", "description": ""}`` *also* leaves it untouched, while
    ``{"name": "x", "description": "changed"}`` writes it. The rule is the zero-value
    guard — the same mechanism as ``Project.Description`` — and only the explicit-empty
    case tells the two apart. Compare ``is_public``, which is a genuine full replace:
    setting it true and then sending ``{"name": "x"}`` puts it back to false. Both cells
    are asserted in ``tests/unit/test_teams_api.py``; dropping the explicit-empty one
    leaves two different rules passing the same tests.

    ``external_id`` is absent on purpose: it is OIDC-provisioned and there is no measured
    route by which a client sets it.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    # `valid:"required"` on teams.go. The tag text is reproduced verbatim inside
    # invalid_fields, so a paraphrase is a wire difference.
    #
    # Defaulted with validate_default rather than declared required, because Go decodes a
    # missing key to the zero value and validates afterwards: "absent" and "empty" are one
    # case upstream and both must answer "name: non zero value required". A required field
    # here would make FastAPI answer 422 for the absent case, a status this API never
    # emits, and 412 for the empty one — two different answers to one upstream case.
    name: Annotated[str, GoValid("required")] = Field(default="", validate_default=True)
    description: str = ""
    is_public: bool = False


class TeamMemberWrite(CaltonModel):
    """The body of ``PUT /teams/{id}/members`` — a **username**, never a user id.

    Upstream binds ``Username string \\`json:"username" param:"user"\\``` and looks the
    user up by name. A body of ``{"user_id": "user3"}`` is therefore not "the wrong key
    for the right idea" — it leaves ``username`` empty and answers
    ``412/2002 ["username: non zero value required"]``, which is a real, upstream,
    entirely credible error message *about a request nobody would send on purpose*. That
    404/412 was measured before this was understood, and it described the probe rather
    than the server.

    The same word is the **path** segment on the two routes that address one member, so
    ``DELETE /teams/1/members/3`` looks up a user named "3" and answers
    ``404/1005 "The user does not exist."``.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    username: Annotated[str, GoValid("required")] = Field(default="", validate_default=True)


class TeamMemberCreated(CaltonModel):
    """``PUT /teams/{id}/members`` and ``POST /teams/{id}/members/{username}/admin``.

    Four keys. ``id`` is the ``team_members`` row id — see the module docstring — and
    there is no embedded user object at all, unlike ``TeamMemberRead``.

    ⚠️ ``admin`` toggling a user who is **not** a member answers ``200`` with
    ``{"id": 0, "username": "...", "admin": true, "created": "0001-01-01T00:00:00Z"}`` and
    writes nothing. The zeroes are the tell that no row was involved; they are also why
    every field here needs a zero default rather than being required.
    """

    id: int = 0
    username: str = ""
    admin: bool = False
    created: Timestamp = ZERO_TIME


# The members list doesn't carry ``permission``; the project-team grant list does.
class TeamWithPermission(TeamRead):
    """``GET /projects/{id}/teams`` entry — ``TeamRead`` plus the grant's ``permission``.

    Upstream's ``TeamWithPermission`` embeds ``Team`` and appends ``Permission`` after
    the embed's serialised fields, so ``permission`` lands last. Subclassing ``TeamRead``
    and adding it as a single field reproduces that, because Pydantic appends the new
    field to the end — the obverse of the inheritance trap in HANDOFF §2, where
    re-declaring an existing field does *not* move it. ``TeamRead``'s order is already
    correct, so the only added field lands in the right place.
    """

    permission: int = 0
