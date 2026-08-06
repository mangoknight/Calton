"""Emoji reactions on tasks and comments.

``GET /{kind}/{id}/reactions`` returns a ``ReactionMap`` — a JSON object mapping
each emoji value to the list of users who reacted with it. ``PUT`` adds the
caller's reaction (idempotent: an existing reaction is returned unchanged) and
``POST /{kind}/{id}/reactions/delete`` removes it.

The wire shape of a single reaction (``models.Reaction``) carries the reacting
``user``, the ``value`` and ``created`` — the row id stays off the wire, so this
schema does not declare one. Measured against the reference server.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from calton.db.types import GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead

#: ``value`` is any UTF-8 text up to 20 characters (``valid:"runelength(0|20)"``
#: on the Go struct, ``maxLength:"20"`` in the swagger). The upper bound is a
#: rune count, not a byte count — see ``db.types._rune_length``.
ReactionValue = Annotated[str, GoValid("runelength(1|20)"), Field(min_length=1)]


class ReactionWrite(CaltonModel):
    """The ``PUT`` / ``POST /delete`` body — only the emoji ``value`` is sent."""

    value: ReactionValue


class ReactionRead(CaltonModel):
    """A single reaction as it appears on the wire (``models.Reaction``)."""

    user: UserRead
    value: str
    created: Timestamp
