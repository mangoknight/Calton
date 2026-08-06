"""Wire shapes for sharing — three PUTs (creates) and three GETs (lists).

The PUT shapes return the **relation-row** id and timestamps; the GET shapes return
the **user/team/share** fields with the grant's ``permission``. Same key names,
different referents — separate classes here for that reason alone. Measured side
by side: adding user6 (user id 6) answered ``id: 66`` (relation row), while the
very next read of the same grant answered ``id: 6`` (user).

GET /projects/{id}/users returns ``UserWithPermission`` (user.User embeds +
permission), the wire shape of upstream's ``ProjectUser.ReadAll``. The same
embeds structure goes for teams (TeamWithPermission = Team + permission), and
the link-share list returns the same shape as the PUT create.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict

from calton.db.types import ZERO_TIME, GoValid, OmitEmptyPtr, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class ProjectUserWrite(CaltonModel):
    """``PUT /projects/{id}/users``.

    ``username``, not ``user_id``: upstream binds ``Username string`` and looks the user
    up by name. An absent username is **404/1005**, not a 412 — the lookup happens before
    any validation, so "no body at all" and "no such user" are one answer.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    username: str = ""
    permission: Annotated[int, GoValid("length(0|2)")] = 0


class ProjectUserCreated(CaltonModel):
    """The 201 body. ``id`` is the relation row — see the module docstring."""

    id: int = 0
    username: str = ""
    permission: int = 0
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class ProjectTeamWrite(CaltonModel):
    """``PUT /projects/{id}/teams``.

    ⚠️ ``right`` is **not** an accepted alias for ``permission``, and I had that as a
    recorded conclusion for several rounds on one observation. Sending ``{"right": 1}``
    answers ``permission: 0``.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    team_id: int = 0
    permission: Annotated[int, GoValid("length(0|2)")] = 0


class ProjectTeamCreated(CaltonModel):
    """The 201 body — the relation row, with no team object embedded."""

    id: int = 0
    team_id: int = 0
    permission: int = 0
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class LinkShareWrite(CaltonModel):
    """``PUT /projects/{id}/shares``."""

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    name: str = ""
    password: str = ""
    permission: Annotated[int, GoValid("length(0|2)")] = 0


class LinkShareCreated(CaltonModel):
    """The link-share wire shape — the PUT create response and (with the row
    carrying the same shape) the GET list entries too."""

    id: int = 0
    hash: str = ""
    name: str = ""
    permission: int = 0
    sharing_type: int = 1
    password: str = ""
    shared_by: UserRead | None = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class UserWithPermission(CaltonModel):
    """``GET /projects/{id}/users`` entry — the user's fields plus the grant's ``permission``.

    Wire order matches Go's ``UserWithPermission`` struct (embeds ``user.User`` +
    ``Permission``). ``email`` and ``bot_owner_id`` carry ``omitempty`` upstream and so
    drop off the wire for human users; the contract test still wants the response_model
    to declare them, hence the ``OmitEmptyPtr`` annotation here.
    """

    id: int
    name: str = ""
    username: str = ""
    email: Annotated[str | None, OmitEmptyPtr()] = None
    bot_owner_id: Annotated[int | None, OmitEmptyPtr()] = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME
    permission: int = 0


class PermissionWrite(CaltonModel):
    """``POST /projects/{id}/{teams,users}/{id}`` — change the permission on an
    existing grant. The subject id comes from the path; only ``permission`` is
    read from the body. Mirrors the create schemas' validation tag, with the
    service re-checking the 0/1/2 range the way the creates do.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    permission: Annotated[int, GoValid("length(0|2)")] = 0


class LinkShareAuthRequest(CaltonModel):
    """``POST /shares/{share}/auth`` — the password for a password-protected link
    share. ``username`` is accepted (upstream's bind targets a struct that carries
    one) but ignored: the share is identified by its hash in the path, not by a
    user. Both fields default to the empty string so an **empty body** is a valid
    request for a share that has no password.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    username: str = ""
    password: str = ""
