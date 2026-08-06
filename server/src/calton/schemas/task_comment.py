"""Task comments — the ``?expand=comments`` embedding and the five T30 endpoints.

Measured on the reference server: a comment carries exactly ``id``, ``comment``,
``author``, ``reactions``, ``created`` and ``updated`` — **no ``task_id``**, even though
the column exists, because the comment is already nested under its task.

``reactions`` has no ``omitempty`` on the Go struct, so the key is always present and is
``null`` when there is nothing to report. It must **not** be grouped with the expand-only
fields, which disappear entirely when they were not asked for.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import ConfigDict, Field, model_validator

from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserEcho, UserRead


class TaskCommentRead(CaltonModel):
    id: int
    comment: str = ""
    author: UserRead | None = None
    #: Always present, never omitted — null when the comment has no reactions.
    reactions: dict[str, Any] | None = None
    created: Timestamp
    updated: Timestamp


class TaskCommentWriteResponse(TaskCommentRead):
    """What ``POST /tasks/{task}/comments/{id}`` answers.

    Same fields as the read shape; ``author`` is re-declared loose because on the update
    path it is **the request's object handed straight back**, and a request may carry as
    little as ``{"id": 901}``. Validating that against ``UserRead`` raises *after the
    comment row has already been updated* — the write succeeds, the client gets a 500, and
    a retry does the edit a second time. Measured: with the read model here,
    ``POST .../comments/950 {"author": {"id": 901, "username": "bob"}}`` answered 500 where
    upstream answers 200 and echoes the partial user.

    Create is not affected and keeps the read model: it resolves the real doer rather than
    echoing, so what it serialises always has the full set of fields.
    """

    author: UserEcho | None = None  # type: ignore[assignment]  # deliberate: a write response is not substitutable for a read one


class TaskCommentWrite(CaltonModel):
    """A comment as clients send it, for both create and update.

    Three fields here look read-only and are not, because upstream binds the whole struct
    from the body and then serialises **that same struct** as the response:

    * ``id`` — tagged ``param:"commentid"``, but Echo binds path parameters *before* the
      body, so a body ``id`` **wins over the path segment**. Measured:
      ``POST /tasks/950/comments/950`` with ``{"id": 952}`` edits comment 952 and answers
      with ``"id": 952``; pointed at someone else's comment it answers 403, so the author
      check follows the effective id rather than the path. Ignoring the body id would be
      the safer-looking choice and would silently disagree on both.
    * ``author`` and ``created`` — echoed back on update. The corpus pins them at ``null``
      and the zero time, which is what a client that sends only ``{"comment": ...}`` gets;
      a read-modify-write client that hands back the whole object it just read gets its
      own values returned instead (measured). Hard-coding the null would be right for the
      corpus case and wrong for every real client.

    ``task_id`` is deliberately absent: it is ``json:"-"`` upstream, so unlike the
    relation endpoints the path segment is the only way to name the task.
    """

    model_config = ConfigDict(strict=True)

    #: ``valid:"dbtext,required"``. ``dbtext`` is Calton's own tag
    #: (``pkg/routes/validation.go:35``), not a govalidator built-in — see
    #: ``db.types.DBTEXT_MAX_BYTES``. Tag order matters: an empty comment passes
    #: ``dbtext`` and fails ``required``, giving "comment: non zero value required".
    #:
    #: ``validate_default`` is load-bearing, not defensive. Pydantic skips validators on a
    #: default, so without it a body of ``{}`` is accepted and answers 201 while
    #: ``{"comment": ""}`` answers 412 — and upstream gives the **same** 412 for both,
    #: because Go decodes the missing key to the zero value and only then validates.
    comment: Annotated[str, GoValid("dbtext,required")] = Field(default="", validate_default=True)
    id: int = 0
    #: ``UserEcho``, not ``UserRead``: this arrives in a **request** body, where a client
    #: may send as little as ``{"id": 901}``. ``UserRead`` requires ``created``/``updated``,
    #: so that body answered **412 with ``invalid_fields: [author.created, author.updated]``**
    #: while upstream answers 200 — a validation strictly tighter than the reference, on a
    #: field that is only ever echoed and never written. Our own frontend never sends
    #: ``author`` at all, so nothing here saw it; the read-modify-write client that posts
    #: the whole object back happened to satisfy the required keys, which is why the
    #: existing round-trip cases stayed green too.
    author: UserEcho | None = None
    reactions: dict[str, list[UserEcho]] | None = None
    created: Timestamp = ZERO_TIME

    @model_validator(mode="before")
    @classmethod
    def _null_means_zero(cls, data: Any) -> Any:
        """Drop explicit nulls so the field default applies.

        ``encoding/json`` leaves a Go field at its zero value when the JSON holds
        ``null``; it is not an error. Read-modify-write clients send ``"reactions": null``
        and ``"author": null`` constantly, and under ``strict=True`` those would otherwise
        be the difference between 200 and 400.
        """
        if not isinstance(data, dict):
            return data
        return {key: value for key, value in data.items() if value is not None}
