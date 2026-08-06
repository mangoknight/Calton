"""The user object as it appears *inside* other resources.

Not the ``GET /user`` payload — that one carries settings and belongs to T14b. This is
the five-field shape Go embeds as ``created_by``, ``owner``, ``author`` and so on
(``pkg/user/user.go``: everything else on the struct is ``json:"-"`` or omitted).

``email`` is deliberately absent: upstream blanks it out before embedding
(``tasks.go:530``), so a schema that carried it would leak addresses through every task.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel

#: ``users.name`` is a nullable column, but a Go ``string`` field has no null: a NULL row
#: value arrives as ``""`` on the wire. Without this the whole response 500s on any user
#: who never set a display name — which is every user created through ``/register``.
NullableText = Annotated[str, BeforeValidator(lambda value: "" if value is None else value)]


class UserRead(CaltonModel):
    id: int
    name: NullableText = ""
    username: NullableText = ""
    created: Timestamp
    updated: Timestamp


class UserEcho(CaltonModel):
    """``UserRead`` with nothing required — a user object arriving in a **write** body.

    The real MCP client updates read-modify-write: it GETs the whole task and POSTs it
    straight back, so every embedded user it read (``created_by``, each entry of
    ``assignees``) comes back into the request. Two consequences, both measured:

    * **Nothing may be required.** ``UserRead`` demands ``created`` and ``updated``; a
      body carrying ``{"id": 901}`` — which is what a hand-written client sends — would
      then 422, and this API never answers 422.
    * **The echo is what was sent, field for field.** Upstream parses into the same user
      struct and serialises it back, so a body carrying the full object echoes the full
      object (``username: "bob"``, its real timestamps) while a body carrying only an id
      echoes zeros. Reading just the id and re-emitting zeros diverges on exactly the
      shape the real client uses.

    Only ``id`` is ever *acted* on; the rest exists to be handed back unchanged.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    id: int = 0
    name: NullableText = ""
    username: NullableText = ""
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


# --- the /user, /users and /register payloads (T14b) -------------------------
#
# Three shapes, deliberately different from each other and from the embedded one
# above. All three are taken from recordings of the Go server
# (``tests/fixtures/go_users.json``) rather than from the ORM model, which would
# leak ``password`` into every one of them:
#
# * ``GET /user`` returns 10 keys and **no email** — the address is not echoed
#   back even to its owner. It nests a ``settings`` object of 12 keys.
# * ``GET /users`` entries are 5 keys — the same field set as ``UserRead`` above,
#   kept separate because this one is a top-level response rather than an
#   embedded object and the two are free to diverge.
# * ``POST /register`` echoes 6 keys and **does** include email.


class UserSettings(BaseModel):
    """The nested ``settings`` object of ``GET /user``.

    ``frontend_settings`` and ``extra_settings_links`` serialise as ``null``
    rather than being omitted — recorded, and the frontend reads both.
    """

    name: str = ""
    email_reminders_enabled: bool = True
    discoverable_by_name: bool = False
    discoverable_by_email: bool = False
    overdue_tasks_reminders_enabled: bool = True
    overdue_tasks_reminders_time: str = "09:00"
    default_project_id: int = 0
    week_start: int = 0
    language: str = ""
    timezone: str = ""
    frontend_settings: str | None = None
    extra_settings_links: str | None = None


class CurrentUser(BaseModel):
    """``GET /user`` — the caller's own account.

    No ``password`` field, and no ``email`` either: upstream does not return the
    address here. Adding one would be a new field in a contract clients diff.
    """

    id: int
    name: str = ""
    username: str
    created: Timestamp
    updated: Timestamp
    settings: UserSettings
    deletion_scheduled_at: Timestamp
    is_local_user: bool = True
    auth_provider: str = "local"
    is_admin: bool = False


class ListedUser(BaseModel):
    """One entry of ``GET /users`` — what other users may see.

    Five keys. Widening this is a privacy change, not a convenience: the endpoint
    is reachable by any authenticated user searching for collaborators.
    """

    id: int
    name: str = ""
    username: str
    created: Timestamp
    updated: Timestamp


class RegisteredUser(BaseModel):
    """``POST /register`` — unlike ``GET /user``, this one echoes the email."""

    id: int
    name: str = ""
    username: str
    email: str
    created: Timestamp
    updated: Timestamp


class RegisterRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    # All three optional at this layer so a missing one produces the measured
    # 400/1004 rather than Pydantic's 412 validation shape. Email is required in
    # practice despite the message naming only username and password.
    username: str | None = None
    password: str | None = None
    email: str | None = None
