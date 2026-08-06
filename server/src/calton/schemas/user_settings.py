"""Wire shapes for the user-settings, TOTP, CalDAV, admin and avatar endpoints.

These routes are not part of the parity corpus, so the bodies are designed rather
than recorded. They stay close to the shapes the rest of the API already uses
(``CaltonModel`` for responses, plain ``BaseModel`` for request bodies) and to the
swagger's own ``definitions`` where one exists — see
``contract/calton-v1-swagger.json`` for ``user.TOTP``, ``user.TOTPPasscode``,
``user.EmailUpdate``, ``v1.UserAvatarProvider``, ``admin.OwnerPatch``,
``admin.IsAdminPatch``, ``admin.StatusPatch`` and ``models.CreateUserBody``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel

# --- general settings --------------------------------------------------------


class GeneralSettingsUpdate(BaseModel):
    """The body of ``POST /user/settings/general``.

    Every field is optional: the upstream handler merges only what was sent, so a
    body carrying a single key updates only that key. A missing key is *not* an
    instruction to clear the column.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    email: str | None = None
    discoverable_by_name: bool | None = None
    discoverable_by_email: bool | None = None
    overdue_tasks_reminders_enabled: bool | None = None
    overdue_tasks_reminders_time: str | None = None
    default_project_id: int | None = None
    week_start: int | None = None
    language: str | None = None
    timezone: str | None = None
    email_reminders_enabled: bool | None = None
    frontend_settings: str | None = None
    extra_settings_links: str | None = None


class EmailUpdate(BaseModel):
    """The body of ``POST /user/settings/email`` — ``user.EmailUpdate`` in the swagger."""

    model_config = ConfigDict(extra="ignore")

    new_email: str = ""
    password: str = ""


class AvatarProviderResponse(BaseModel):
    """``GET /user/settings/avatar`` — ``v1.UserAvatarProvider``."""

    avatar_provider: str = "default"


class AvatarProviderUpdate(BaseModel):
    """The body of ``POST /user/settings/avatar``."""

    model_config = ConfigDict(extra="ignore")

    avatar_provider: str = "default"


# --- TOTP --------------------------------------------------------------------


class TOTPResponse(CaltonModel):
    """``GET /user/settings/totp`` and ``POST /user/settings/totp/enroll`` —
    ``user.TOTP`` in the swagger: ``enabled``, ``secret``, ``url``."""

    enabled: bool = False
    secret: str = ""
    url: str = ""


class TOTPPasscode(BaseModel):
    """``user.TOTPPasscode`` — the body of ``POST /user/settings/totp/enable``."""

    model_config = ConfigDict(extra="ignore")

    passcode: str = ""


class TOTPDisableBody(BaseModel):
    """The body of ``POST /user/settings/totp/disable``.

    Upstream reuses ``user.Login`` here (``password``), but the route only acts on
    the password. Declaring only what is used keeps a read-modify-write client from
    having to send a username it does not have.
    """

    model_config = ConfigDict(extra="ignore")

    password: str = ""


# --- CalDAV tokens -----------------------------------------------------------


class CalDAVTokenRead(CaltonModel):
    """One entry of ``GET /user/settings/token/caldav`` — ``user.Token``."""

    id: int
    created: Timestamp = ZERO_TIME


class CalDAVTokenCreated(CaltonModel):
    """``PUT /user/settings/token/caldav`` — ``user.Token`` with the plaintext token,
    shown once."""

    id: int
    token: str = ""
    created: Timestamp = ZERO_TIME


# --- admin -------------------------------------------------------------------


class AdminOverview(BaseModel):
    """``GET /admin/overview`` — row counts across the major tables."""

    total_users: int = 0
    total_projects: int = 0
    total_tasks: int = 0
    total_teams: int = 0
    total_labels: int = 0
    total_attachments: int = 0


class AdminProjectRead(CaltonModel):
    """One entry of ``GET /admin/projects``."""

    id: int
    title: str = ""
    identifier: str | None = None
    owner_id: int = 0
    is_archived: bool = False
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class OwnerPatch(BaseModel):
    """``admin.OwnerPatch`` — the body of ``PATCH /admin/projects/{id}/owner``."""

    model_config = ConfigDict(extra="ignore")

    owner_id: int


class AdminUserRead(CaltonModel):
    """One entry of ``GET /admin/users``.

    Unlike the public ``UserRead``, the admin list **does** carry email and the
    status/admin flags — the whole point of the panel is to see and change them.
    """

    id: int
    name: str = ""
    username: str = ""
    email: str = ""
    status: int = 0
    is_admin: bool = False
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class AdminUserCreate(BaseModel):
    """The body of ``POST /admin/users`` — ``models.CreateUserBody``."""

    model_config = ConfigDict(extra="ignore")

    username: str = ""
    email: str = ""
    password: str = ""
    name: str | None = None
    is_admin: bool = False


class AdminIsAdminPatch(BaseModel):
    """``admin.IsAdminPatch`` — the body of ``PATCH /admin/users/{id}/admin``.

    A pointer upstream distinguishes "false" from "omitted"; here the field is
    required so an empty body is rejected rather than silently demoting the user.
    """

    model_config = ConfigDict(extra="ignore")

    is_admin: bool


class AdminStatusPatch(BaseModel):
    """``admin.StatusPatch`` — the body of ``PATCH /admin/users/{id}/status``."""

    model_config = ConfigDict(extra="ignore")

    status: int = Field(..., ge=0, le=3)
