"""Task relations — request and response shapes for the two T31 endpoints.

The created relation carries **exactly five keys**: ``task_id``, ``other_task_id``,
``relation_kind``, ``created_by`` and ``created``. There is no ``id`` — the row has a
primary key but it is ``json:"-"``, which is why the delete route addresses a relation by
the ``(task, kind, other task)`` triple instead.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, model_validator

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class RelationKind(StrEnum):
    """``RelationKind.isValid()`` (``task_relation.go:66``), minus ``unknown``.

    The strings are a wire contract and the ``enum:`` tag on the Go struct repeats them.
    ``unknown`` is deliberately not a member: it exists upstream only as the value
    ``getInverseRelation`` returns for an input it does not recognise, and it never
    validates, so modelling it here would make an invalid kind representable.
    """

    SUBTASK = "subtask"
    PARENTTASK = "parenttask"
    RELATED = "related"
    DUPLICATEOF = "duplicateof"
    DUPLICATES = "duplicates"
    BLOCKING = "blocking"
    BLOCKED = "blocked"
    PRECEDES = "precedes"
    FOLLOWS = "follows"
    COPIEDFROM = "copiedfrom"
    COPIEDTO = "copiedto"


#: ``getInverseRelation`` (``task_relation.go:110``). Every relation is stored **twice**,
#: once per direction, so this table is what the create and delete paths both write
#: through. ``related`` is its own inverse; the other ten pair up.
INVERSE_RELATION: dict[RelationKind, RelationKind] = {
    RelationKind.SUBTASK: RelationKind.PARENTTASK,
    RelationKind.PARENTTASK: RelationKind.SUBTASK,
    RelationKind.RELATED: RelationKind.RELATED,
    RelationKind.DUPLICATEOF: RelationKind.DUPLICATES,
    RelationKind.DUPLICATES: RelationKind.DUPLICATEOF,
    RelationKind.BLOCKING: RelationKind.BLOCKED,
    RelationKind.BLOCKED: RelationKind.BLOCKING,
    RelationKind.PRECEDES: RelationKind.FOLLOWS,
    RelationKind.FOLLOWS: RelationKind.PRECEDES,
    RelationKind.COPIEDFROM: RelationKind.COPIEDTO,
    RelationKind.COPIEDTO: RelationKind.COPIEDFROM,
}

#: The two kinds whose inverse points the other way up a hierarchy, and therefore the only
#: two ``Create`` runs the cycle check for (``task_relation.go:243``). A ``blocking`` loop
#: is allowed: measured, 950 → 954 and 954 → 950 both answer 201.
HIERARCHICAL_KINDS = frozenset({RelationKind.SUBTASK, RelationKind.PARENTTASK})


class TaskRelationWrite(CaltonModel):
    """A relation as clients send it.

    ``relation_kind`` is a plain ``str`` rather than the enum on purpose. An unrecognised
    value is **not** a binding failure upstream — it binds fine and is refused by
    ``CanCreate`` as 400/4007 ``"The task relation is invalid."``. Declaring the enum here
    would answer 412/2002 with ``invalid_fields`` instead, for the single most likely bad
    input this endpoint receives. Measured: ``"nosuch"`` and a missing key give the *same*
    400/4007.

    ``task_id`` is accepted for the same reason it is on ``TaskCommentWrite``: Echo binds
    the path parameter first and the body second, so a body ``task_id`` **replaces** the
    path segment. Measured: ``PUT /tasks/951/relations`` with ``{"task_id": 953}`` writes
    the relation on task 953 and answers ``"task_id": 953``. Permissions are then checked
    against 953, so this is a wire quirk rather than an escalation — but an implementation
    that ignores the body value disagrees on both the effect and the response.
    """

    model_config = ConfigDict(strict=True)

    task_id: int | None = None
    other_task_id: int = 0
    relation_kind: str = ""

    @model_validator(mode="before")
    @classmethod
    def _null_means_zero(cls, data: Any) -> Any:
        """Drop explicit nulls so the field default applies (see ``TaskCommentWrite``)."""
        if not isinstance(data, dict):
            return data
        return {key: value for key, value in data.items() if value is not None}


class TaskRelationCreated(CaltonModel):
    """The 201 body. Exactly five keys — see the module docstring."""

    task_id: int
    other_task_id: int
    relation_kind: str
    #: A full user object, unlike the assignee bulk echo, which returns empty shells.
    created_by: UserRead | None = None
    #: A real timestamp, unlike the single-assignee echo, which returns the zero time.
    created: Timestamp = ZERO_TIME
