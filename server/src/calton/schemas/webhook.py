"""Webhook wire shapes.

Two things here are not what the swagger or the field names suggest, and both were
measured rather than read:

* **the secret never comes back.** ``secret``, ``basic_auth_user`` and
  ``basic_auth_password`` are accepted on create and then masked to ``""`` on every
  response, including the 201 that just took them. A client cannot read back what it
  stored.
* **the update writes exactly one column.** ``POST`` requires ``target_url`` and rejects
  a body without it — and then discards it. Only ``events`` reaches the database.

Both of those live in the service; what matters here is that no schema pretends
otherwise, because a write model that looks like a full replace invites an
implementation that performs one.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field

from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserEcho, UserRead


class WebhookRead(CaltonModel):
    """A webhook target, as all four routes return it.

    ``user_id`` is present and zero for a project webhook. Upstream shares one struct
    between project webhooks and the user-level ones (``/user/settings/webhooks``, not in
    scope), so the field is always serialised; omitting it here would be tidier and would
    drop a key clients receive today.

    ``secret`` and the basic-auth pair are always ``""`` — see the module docstring. They
    are declared because upstream sends the keys, not because they ever carry a value.
    """

    id: int
    target_url: str = ""
    events: list[str] = Field(default_factory=list)
    project_id: int = 0
    user_id: int = 0
    secret: str = ""
    basic_auth_user: str = ""
    basic_auth_password: str = ""
    created_by: UserRead | None = None
    created: Timestamp = ZERO_TIME
    updated: Timestamp = ZERO_TIME


class WebhookWrite(CaltonModel):
    """The body of ``PUT`` and ``POST`` on a project's webhooks.

    ``target_url`` and ``events`` are both required in upstream's non-zero sense, on
    **both** verbs — a create or an update missing either answers 412/2002 with
    ``["target_url: non zero value required"]`` or ``["events: non zero value
    required"]``. That is worth stating for the update in particular, where
    ``target_url`` is required and then ignored (see ``webhook_service.update``): the
    validation and the persistence disagree upstream, and copying only the sensible half
    of that would change a 412 into a 200.

    An unknown event name is a different failure: 412/2002 with a bare ``["events"]`` and
    **no message after the field name**, because upstream builds that one by hand rather
    than through the validator. Reproducing it needs the field list, not a ``GoValid``
    tag — see ``core.errors.InvalidFieldError``.
    """

    model_config = ConfigDict(
        strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
    )

    target_url: Annotated[str, GoValid("required")] = Field(default="", validate_default=True)
    events: Annotated[list[str], GoValid("required")] = Field(
        default_factory=list, validate_default=True
    )
    secret: str = ""
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # --- echoed, never acted on ------------------------------------------------
    #
    # ⚠️ Here because the **update response is the bound struct**, so whatever the request
    # carried in these comes straight back out. A read-modify-write client — which is what
    # the real MCP client is — GETs the webhook and POSTs the whole object back, so its
    # update answers the real ``created`` and ``created_by`` it read, while a hand-written
    # client sending only ``{target_url, events}`` gets the zero time and null. One
    # endpoint, two different bodies, decided entirely by what was sent.
    #
    # Declaring them is what makes both cases reproducible. Leaving them out — they are
    # read-only fields, so omitting them from a *write* schema is the natural choice —
    # makes every RMW update answer zeros, and that difference is invisible to any client
    # that does not round-trip.
    created: Timestamp = ZERO_TIME
    created_by: UserEcho | None = None
