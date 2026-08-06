"""P1-1 — ``omitempty`` is two rules, and one implementation cannot serve both.

Go's ``omitempty`` omits a nil pointer *and* an empty slice/map. Pydantic has neither
behaviour: ``exclude_none`` covers only the first half, so an empty collection goes out as
``[]`` where upstream sends no key at all.

Merging the two into a single "omit anything falsy" marker is the obvious simplification
and is wrong in a way no parity case on these fields would reveal: it would also delete
``0``, ``false`` and ``""`` from fields that are not tagged ``omitempty`` at all.

**Every value in this file is picked so a wrong implementation gives a different answer**
(practice 4). In particular the ptr cases use ``0``/``False``/``""`` rather than some
arbitrary truthy value — against a falsy-based implementation a truthy value is a fixed
point and the test would pass no matter what.
"""

from __future__ import annotations

from typing import Annotated, Any

from calton.db.types import OmitEmptyCollection, OmitEmptyPtr
from calton.schemas.base import CaltonModel


class Sample(CaltonModel):
    #: Tagged ``omitempty`` on a pointer: only None disappears.
    comment_count: Annotated[int | None, OmitEmptyPtr()] = None
    subscription: Annotated[dict[str, Any] | None, OmitEmptyPtr()] = None
    #: Tagged ``omitempty`` on a slice/map: nil *and* empty disappear.
    buckets: Annotated[list[int] | None, OmitEmptyCollection()] = None
    labels: Annotated[dict[str, int] | None, OmitEmptyCollection()] = None
    #: Untagged. Present always, whatever it holds.
    percent_done: float = 0.0
    done: bool = False
    title: str = ""


def dumped(**values: Any) -> dict[str, Any]:
    return Sample(**values).model_dump(mode="json")


class TestOmitEmptyCollection:
    def test_an_empty_list_drops_the_key_entirely(self) -> None:
        """The half a None-only implementation misses.

        ``[]`` rather than ``None`` is the whole point: under ``exclude_none`` or an
        ``is None`` test this case emits ``"buckets": []`` and upstream emits nothing.
        """
        assert "buckets" not in dumped(buckets=[])

    def test_an_empty_dict_drops_the_key_entirely(self) -> None:
        assert "labels" not in dumped(labels={})

    def test_none_also_drops_the_key(self) -> None:
        assert "buckets" not in dumped(buckets=None)

    def test_a_populated_collection_survives(self) -> None:
        """Guards the other direction: a marker that dropped everything would pass
        every test above and delete real data."""
        assert dumped(buckets=[7])["buckets"] == [7]
        assert dumped(labels={"a": 1})["labels"] == {"a": 1}


class TestOmitEmptyPtr:
    def test_none_drops_the_key(self) -> None:
        assert "comment_count" not in dumped(comment_count=None)
        assert "subscription" not in dumped(subscription=None)

    def test_zero_is_kept_because_a_pointer_to_zero_is_not_nil(self) -> None:
        """``0`` is the discriminating value.

        Go omits a *nil* pointer, not a pointer to a zero value, so ``comment_count: 0``
        goes on the wire. A falsy-based implementation drops it and this is the only test
        that would notice.
        """
        assert dumped(comment_count=0)["comment_count"] == 0

    def test_an_empty_dict_is_kept_on_a_ptr_field(self) -> None:
        """The two markers must not be interchangeable.

        ``subscription`` is a pointer, so an empty object is still a real value. If this
        field had been given OmitEmptyCollection the key would vanish here — which is why
        the markers name the Go tag they stand for rather than the Python type.
        """
        assert dumped(subscription={})["subscription"] == {}


class TestUntaggedFieldsAreNeverOmitted:
    """The damage a merged 'falsy is omitted' marker would do, asserted directly."""

    def test_zero_false_and_empty_string_all_stay(self) -> None:
        body = dumped(percent_done=0.0, done=False, title="")

        assert body["percent_done"] == 0.0
        assert body["done"] is False
        assert body["title"] == ""


def test_the_markers_do_not_erase_the_openapi_schema() -> None:
    """The omission serializer must not cost the model its declared fields.

    A wrap serializer returning ``dict[str, Any]`` makes Pydantic document the model as a
    bare object, which is the failure convention C-1 exists to prevent: the contract diff
    then compares against an empty set and passes vacuously.
    """
    properties = Sample.model_json_schema(mode="serialization")["properties"]

    assert {"comment_count", "subscription", "buckets", "labels"} <= properties.keys()
