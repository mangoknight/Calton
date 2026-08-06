"""Project request and response schemas.

``ProjectWrite`` deliberately **does not declare** ``views``.

``Project.Views`` is ``json:"views"`` with no ``omitempty`` and is read-only, so every
project response carries a ``views[]`` array whose ``view_kind`` is a *string*. An MCP
client doing read-modify-write posts that whole object straight back. If this schema
declared ``views``, that echo would be parsed — and under ``strict=True`` a nested string
enum is a 422 on every update. Not declaring it means ``extra="ignore"`` drops the array
before validation ever sees it. The requirement is met by not accepting the field, not by
converting it.

The title is ``min_length=1`` and nothing more. Upstream's ``required`` tests for a
non-zero value, not a non-blank one, so a title of spaces is accepted (measured: 201).
Adding a ``strip()`` here would reject input the reference server takes.

**Two fields answer differently on the collection than on the item**, both measured on the
reference server and neither guessable from the struct:

============  ==========================  =============================
field         ``GET /projects``           ``GET /projects/{id}``, ``PUT``
============  ==========================  =============================
``views``     ``null`` (populated when    ``[]`` when the project has
              the project has views)      none
``max_permission``  ``null``              ``0`` — *always*, even for the
                                          owner
============  ==========================  =============================

So ``max_permission`` in the body is not the caller's permission and must not be filled in
with it: the real value travels in the ``x-max-permission`` header, where the same request
that returns a body ``0`` returns a header ``2``. Populating the field "correctly" would
be a divergence on every project response. Both fields are therefore nullable and neither
is omitted — the key is always present.

``subscription`` is declared and never emitted: it is ``json:"subscription,omitempty"`` on
a pointer that Phase 1 never populates, so upstream sends no key (measured — it is absent
from every recorded response). It has to be *declared* anyway, because the contract diff
compares against upstream's documented field set. That is exactly what ``OmitEmptyPtr``
is for: declared in the schema, absent from the JSON.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from calton.db.types import GoFloat, GoValid, OmitEmptyPtr, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.project_view import ProjectViewRead
from calton.schemas.user import UserRead


class ProjectWrite(BaseModel):
    """What a client may send. Extra keys are ignored, never rejected."""

    model_config = ConfigDict(strict=True, extra="ignore")

    # Tags copied verbatim from pkg/models/project.go:43,47,49 — the text is reproduced
    # in invalid_fields, so a paraphrase is a wire difference.
    #
    # ⚠️ ``title`` has a **default** despite carrying ``required``. Go decodes a missing
    # key to the zero value and validates after, so absent and empty are one case
    # upstream; both must report "non zero value required". Marking it required in
    # Pydantic instead makes a missing key a ``missing`` error with different wording.
    # validate_default: Pydantic skips validators on a default, so without it a body with
    # no title at all would be accepted. Upstream validates after decoding, where a
    # missing key is already the zero value — so the default is exactly what has to be
    # checked.
    #: ⚠️ **A body ``id`` shadows the path segment**, and that is upstream's behaviour, not
    #: a convenience. Echo binds path parameters *before* the body, so the body wins.
    #: Measured: ``POST /projects/906`` carrying ``{"id": 907}`` answers **404/3001** when
    #: 907 does not exist, rather than editing 906. Dropping the field (which
    #: ``extra="ignore"`` did silently) makes us write to the object named in the path — a
    #: *different row* — while answering 200 either way, so no status code separates them.
    #:
    #: On **create** it is not used to create anything but is still looked up first; see
    #: ``services.project_crud.ProjectService.create``.
    id: int = 0
    title: Annotated[str, GoValid("required,runelength(1|250)")] = Field(
        default="", validate_default=True
    )
    description: str = ""
    identifier: Annotated[str, GoValid("runelength(0|10)")] = ""
    hex_color: Annotated[str, GoValid("runelength(0|7)")] = ""
    #: ``None`` means "not sent". An explicit ``null`` and an omitted key are the same
    #: thing here, because Go cannot tell them apart either — both arrive as a nil
    #: pointer. Reading presence from ``model_fields_set`` instead would treat a null as
    #: "detach to the top level" and move the project.
    parent_project_id: int | None = None
    is_archived: bool = False
    is_favorite: bool = False
    position: GoFloat = 0.0


class ProjectOwner(BaseModel):
    """The nested user object a project carries."""

    id: int
    name: str = ""
    username: str = ""
    created: Timestamp | None = None
    updated: Timestamp | None = None


class ProjectUserRead(UserRead):
    """A user in the ``projectusers`` list.

    Adds the two fields upstream *documents* on this route but never actually sends.
    Both are ``omitempty`` on values Phase 1 leaves empty, and the reference server omits
    them even for a user who does have an address: a freshly registered account with
    ``em809209@example.test`` still comes back as the same five keys. Declared so the
    contract diff has something to compare, ``OmitEmptyPtr`` so the wire shape is
    unchanged.

    ⚠️ ``email`` staying absent is a **privacy property**, not an oversight — this route
    lists everyone with access to a project, so filling the field in would hand every
    collaborator's address to anyone who can read it. ``test_projects_api`` asserts the
    key is missing; if that test ever goes red, the answer is not to update it.
    """

    email: Annotated[str | None, OmitEmptyPtr()] = None
    bot_owner_id: Annotated[int | None, OmitEmptyPtr()] = None

    @field_validator("email", "bot_owner_id", mode="before")
    @classmethod
    def _never_populate(cls, _value: Any) -> None:
        """Blank both fields whatever the model was built from.

        Not belt-and-braces. This model is normally built with
        ``model_validate(user, from_attributes=True)``, which reads ``User.email`` — so
        merely *declaring* the field to satisfy the contract diff turned it into a real
        disclosure the moment a seeded user had an address. Caught by
        ``test_no_email_is_disclosed``; without the guard the leak returns the next time
        anyone builds one from an ORM object.

        ⚠️ A **field** validator, not a model one. ``model_validator(mode="after")`` wraps
        the model's core schema in another layer, and ``CaltonModel``'s serialization
        hook unwraps exactly one — so it emptied this model's OpenAPI schema entirely and
        the contract diff reported all seven fields missing. Same vacuous-contract
        failure that hook exists to prevent, reintroduced from above.
        """
        return None


class ProjectRead(CaltonModel):
    """What a project looks like on the wire."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str = ""
    identifier: str = ""
    hex_color: str = ""
    parent_project_id: int = 0
    owner: ProjectOwner | None = None
    is_archived: bool = False
    #: Always present and null on a project with no background (measured).
    background_information: str | None = None
    background_blur_hash: str = ""
    #: ⚠️ Declaration order here is the wire order, and this field's position is
    #: measured, not chosen: upstream emits it **after** ``background_blur_hash``,
    #: not next to ``is_archived`` where it reads as if it belongs. Grouping the
    #: two ``is_*`` flags is the natural edit and it is wrong.
    is_favorite: bool = False
    position: GoFloat = 0.0
    #: ``null`` on the collection, ``[]`` on the item. Not omitted in either case.
    views: list[ProjectViewRead] | None = None
    #: Always 0 on the item and null on the collection — never the caller's permission.
    #: See the module docstring.
    max_permission: int | None = None
    #: Declared for the contract, never emitted. See the module docstring.
    subscription: Annotated[dict[str, Any] | None, OmitEmptyPtr()] = None
    created: Timestamp | None = None
    updated: Timestamp | None = None
