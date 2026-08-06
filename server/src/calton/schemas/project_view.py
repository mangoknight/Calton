"""Project view wire shapes.

Two fields are integers in the database and **strings on the wire**: ``view_kind`` and
``bucket_configuration_mode``. They are the ``enums:`` half of upstream's enum tags — the
other spelling, ``enum:`` (singular), tags ``relation_kind`` and belongs to T31. The two
tag spellings have **completely disjoint hit sets**, so grepping for one finds neither of
the other's fields.

Because a write schema here accepts both of them, this is where the shared string-enum
conversion is put to work: ``strict=True`` refuses ``str -> Enum`` outright, so without
``StrEnumValue`` every real request would 422 — clients send ``"kanban"``, never the
member object. An unrecognised name is measured **400/2004**, a bind failure, not the
412/2002 that an empty title gets; the two validation exits are different code paths
upstream and :mod:`calton.api.v1.views` keeps them apart.

⚠️ **``filter`` and ``bucket_configuration`` are JSON documents in their columns, not
scalars.** ``project_views.filter`` holds the whole marshalled ``TaskCollection`` —

    {"s":"","sort_by":null,"order_by":null,"filter":"done = false","filter_include_nulls":false}

— and ``bucket_configuration`` holds a JSON array. Reading either as a bare string puts
the raw JSON text on the wire inside the object's own ``filter`` key. That was measured
happening: :func:`view_filter_of` used to wrap the column value as though it were the
expression, so every project carrying a List view served its stored JSON as a string.
:func:`stored_filter_of` and :func:`view_filter_of` are the pair that must stay inverse.

**NULL and the empty filter are different values**, which is why ``view_filter_of``
cannot fold them together. Measured on all six input forms:

===========================  ==================  ==========================
request body                 column              wire
===========================  ==================  ==========================
``filter`` key omitted       SQL NULL            ``null``
``"filter": null``           SQL NULL            ``null``
``"filter": {}``             full JSON, ``""``   the **object**, ``filter: ""``
``"filter": {"filter": ""}`` full JSON, ``""``   the object
``"filter": {"s": "abc"}``   full JSON           the object, ``s`` preserved
a bare string                —                   **400/2004**, never stored
===========================  ==================  ==========================

``bucket_configuration`` is the same story with one extra wrinkle: its absent value is
the JSON literal ``null`` in the column rather than SQL NULL, and ``[]`` is stored and
served as an empty array distinct from it. Both spellings of "absent" must read back as
``None`` or a view written by one path serialises differently from one written by the
other.

**``filter`` is a full-replace field and is NOT an AC-6 exception.** ``Update()`` writes
it into ``Cols(...)`` unconditionally and guards nothing (``project_view.go:443``), so a
POST that omits it clears it to NULL. Measured: omitted → null, explicit null → null,
explicit value → written. That looks like data loss and is the specified behaviour; the
genuine exceptions are ``Project.ParentProjectID`` and ``SavedFilter.Filters``, both of
which have a nil guard this field does not.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any

from pydantic import ConfigDict, Field

from calton.db.types import GoFloat, GoValid, StrEnumValue, Timestamp
from calton.schemas.base import CaltonModel


class ViewKind(StrEnum):
    """``project_view.go``. Stored as the index, sent as the name."""

    LIST = "list"
    GANTT = "gantt"
    TABLE = "table"
    KANBAN = "kanban"


class BucketConfigurationMode(StrEnum):
    NONE = "none"
    MANUAL = "manual"
    FILTER = "filter"


#: Index in the database <-> name on the wire, in declaration order. Kept as tuples rather
#: than dicts so the index *is* the position and the two can never disagree.
VIEW_KINDS: tuple[ViewKind, ...] = (
    ViewKind.LIST,
    ViewKind.GANTT,
    ViewKind.TABLE,
    ViewKind.KANBAN,
)
BUCKET_MODES: tuple[BucketConfigurationMode, ...] = (
    BucketConfigurationMode.NONE,
    BucketConfigurationMode.MANUAL,
    BucketConfigurationMode.FILTER,
)


def kind_to_index(kind: ViewKind | str) -> int:
    return VIEW_KINDS.index(ViewKind(kind))


def index_to_kind(index: int | None) -> ViewKind:
    stored = index or 0
    return VIEW_KINDS[stored] if 0 <= stored < len(VIEW_KINDS) else ViewKind.LIST


def mode_to_index(mode: BucketConfigurationMode | str) -> int:
    return BUCKET_MODES.index(BucketConfigurationMode(mode))


def index_to_mode(index: int | None) -> BucketConfigurationMode:
    stored = index or 0
    return BUCKET_MODES[stored] if 0 <= stored < len(BUCKET_MODES) else BucketConfigurationMode.NONE


class ViewFilter(CaltonModel):
    """A view's filter: a JSON document in the column, this object on the wire.

    The frontend reads ``filter.filter``, and ``s`` round-trips too — a view created with
    ``{"s": "abc"}`` reads back with it, so this is a real nested object rather than a
    wrapper around one string. Field order matches the column's key order.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    s: str = ""
    sort_by: list[str] | None = None
    order_by: list[str] | None = None
    filter: str = ""
    filter_include_nulls: bool = False


class BucketConfiguration(CaltonModel):
    """One entry of a filtered view's ``bucket_configuration`` array."""

    model_config = ConfigDict(strict=True, extra="ignore")

    title: str = ""
    filter: ViewFilter | None = None


def view_filter_of(stored: str | None) -> ViewFilter | None:
    """Parse a view's stored filter document, or ``None`` when the column is NULL.

    ⚠️ Only NULL means "no filter". A stored document whose ``filter`` is the empty string
    is served as an object, so ``if not stored`` is the wrong test on the *parsed* value —
    it is the right one here only because an empty column string is not a shape upstream
    ever writes.
    """
    if stored is None or stored == "":
        return None
    try:
        document = json.loads(stored)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return ViewFilter.model_validate(document)


def stored_filter_of(view_filter: ViewFilter | None) -> str | None:
    """The column value for a filter — the inverse of :func:`view_filter_of`."""
    if view_filter is None:
        return None
    return json.dumps(view_filter.model_dump(mode="json"), separators=(",", ":"))


def bucket_configuration_of(stored: str | None) -> list[BucketConfiguration] | None:
    """Parse the stored bucket configuration array.

    Both SQL NULL and the JSON literal ``null`` mean absent: upstream writes the literal
    on every create and update, but a row loaded straight into the table has SQL NULL.
    Reading only one of the two makes a view's shape depend on how it was written.
    """
    if stored is None or stored == "":
        return None
    try:
        document = json.loads(stored)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, list):
        return None
    return [BucketConfiguration.model_validate(entry) for entry in document]


def stored_bucket_configuration_of(entries: list[BucketConfiguration] | None) -> str:
    """The column value for a bucket configuration.

    Returns the JSON literal ``"null"`` rather than ``None`` for an absent configuration,
    because that is what upstream stores — and ``[]`` stays an empty array, which is a
    different value on the wire.
    """
    if entries is None:
        return "null"
    return json.dumps([entry.model_dump(mode="json") for entry in entries], separators=(",", ":"))


class ProjectViewWrite(CaltonModel):
    """What a client may send. Extra keys are ignored, never rejected.

    ⚠️ ``id`` and ``project_id`` are **accepted from the body and override the path**.
    Echo binds the path parameters onto the struct first and then unmarshals the body over
    the top, so the body wins wherever it names the same field. Measured, and it is not
    theoretical: ``POST /projects/950/views/{a}`` with ``{"id": b}`` updates view *b*, and
    ``PUT /projects/950/views`` with ``{"project_id": 903}`` is refused against 903 rather
    than allowed against 950. Dropping these two fields would make the path authoritative,
    which is the safer design and the wrong one — see :mod:`calton.api.v1.views`.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    #: Overrides the path on update; ignored on create, where upstream zeroes it before
    #: the insert (``createProjectView`` sets ``p.ID = 0``).
    id: int | None = None
    #: Overrides the path everywhere, including for the permission check.
    project_id: int | None = None
    #: Tag copied from project_view.go:156. Default + validate_default because Go
    #: validates after decoding, where a missing key is already the zero value.
    title: Annotated[str, GoValid("required,runelength(1|250)")] = Field(
        default="", validate_default=True
    )
    #: Omitting it is measured 201 with the view created as a List — the zero value, not
    #: an error. An unrecognised name is 400 (a bind failure), and so is an integer:
    #: the wire type is the name, never the index.
    view_kind: StrEnumValue(ViewKind) = ViewKind.LIST  # type: ignore[valid-type]
    #: Omitting this clears it. See the module docstring before "fixing" that.
    filter: ViewFilter | None = None
    position: float = 0.0
    bucket_configuration_mode: StrEnumValue(BucketConfigurationMode) = (  # type: ignore[valid-type]
        BucketConfigurationMode.NONE
    )
    bucket_configuration: list[BucketConfiguration] | None = None


class ProjectViewRead(CaltonModel):
    """A view on the wire.

    The collection and the item serialise **identically** here — measured key-set and
    value-by-value, because projects do *not*: there ``views`` and ``max_permission``
    differ between the two shapes. The similarity is not something to assume from one
    resource to the next, and it does not extend to the headers: the collection carries
    the two pagination headers and no ``x-max-permission``, the item carries
    ``x-max-permission`` and neither pagination header.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str = ""
    project_id: int
    view_kind: ViewKind
    filter: ViewFilter | None = None
    #: ``GoFloat``: Go renders an integral float64 as ``100``, Python as ``100.0``.
    #: Measured on every view, every project and every task — and invisible to both
    #: verification layers, because normalize.py maps ``position`` to <POS> and a
    #: parsed comparison has ``100 == 100.0``. Only raw bytes show it.
    position: GoFloat = 0.0
    bucket_configuration_mode: BucketConfigurationMode
    bucket_configuration: list[BucketConfiguration] | None = None
    default_bucket_id: int = 0
    done_bucket_id: int = 0
    updated: Timestamp | None = None
    created: Timestamp | None = None


def view_read(view: Any, *, created: Any = None) -> ProjectViewRead:
    """Serialise a stored view, converting both integer enums to their names.

    ``created`` is an override rather than always being read off the row because the
    update response carries the **zero time** there while the row keeps its real value.
    Callers that want the row's own value pass nothing.
    """
    return ProjectViewRead(
        id=view.id,
        title=view.title or "",
        project_id=view.project_id,
        view_kind=index_to_kind(view.view_kind),
        filter=view_filter_of(view.filter),
        position=view.position or 0,
        bucket_configuration_mode=index_to_mode(view.bucket_configuration_mode),
        bucket_configuration=bucket_configuration_of(view.bucket_configuration),
        default_bucket_id=view.default_bucket_id or 0,
        done_bucket_id=view.done_bucket_id or 0,
        created=view.created if created is None else created,
        updated=view.updated,
    )
