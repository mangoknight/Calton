"""Integral floats go on the wire without a decimal point, as Go writes them.

Go's ``encoding/json`` renders a ``float64`` holding an integral value as ``0``;
Python's ``json`` renders ``0.0``. The two parse to numbers that compare equal,
which is why this survived 363 corpus cases: ``diff_paths(normalize(go),
normalize(calton))`` returns ``[]`` for ``0`` vs ``0.0``, and the contract diff
compares which fields exist rather than how they are spelled.

**So these tests are the only thing guarding it.** The parity harness is
structurally blind here until its comparison is taught to look at the rendering,
and that work belongs to whoever owns `normalize`/`compare`. Do not delete these
on the grounds that "the corpus covers it" — the corpus is exactly what does not.

Every assertion below is on the **serialized bytes**, not on a parsed value. A
test that round-trips through ``json.loads`` cannot fail for the bug it is
supposed to catch.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from calton.db.types import GoFloat


class _Model(BaseModel):
    value: GoFloat = 0


def render(value: float) -> str:
    """The JSON text a response body would carry for this value."""
    return json.dumps(_Model(value=value).model_dump(mode="json")["value"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0"),
        (0, "0"),
        (1.0, "1"),
        (-1.0, "-1"),
        (100.0, "100"),
        # Non-integral values keep their decimal part: Go writes these the same
        # way, so a fix that turned every float into an int would be wrong in the
        # other direction. percent_done is a real 0..1 field, so 0.5 is not a
        # hypothetical.
        (0.5, "0.5"),
        (-0.25, "-0.25"),
        (1.5, "1.5"),
    ],
)
def test_a_float_is_rendered_the_way_go_renders_it(value: float, expected: str) -> None:
    assert render(value) == expected


def test_the_zero_case_is_not_merely_equal_but_spelled_the_same() -> None:
    """The assertion this whole file exists for, stated so it cannot be softened.

    ``0 == 0.0`` in Python, so an equality assertion here passes with or without
    the fix. Comparing the *text* is the only version that can fail.
    """
    assert render(0.0) == "0"
    assert render(0.0) != "0.0"


def test_a_task_response_spells_position_and_percent_done_like_go() -> None:
    """End to end through the real schema, not just the annotated type.

    Applying `GoFloat` to the type and forgetting to apply it to the fields would
    leave the test above green, which is the shape of failure this project keeps
    hitting: the mechanism works and nothing uses it.
    """
    from calton.schemas.task import TaskRead

    fields = TaskRead.model_fields
    for name in ("position", "percent_done"):
        assert name in fields, f"TaskRead lost its {name} field"

    body = json.dumps(
        {
            k: v
            for k, v in TaskRead.model_construct(position=0.0, percent_done=0.0)
            .model_dump(mode="json")
            .items()
            if k in ("position", "percent_done")
        },
        sort_keys=True,
    )
    assert body == '{"percent_done": 0, "position": 0}', body


def test_the_declared_type_stays_number() -> None:
    """The rendering fix must not widen the contract.

    Returning ``int | float`` makes Pydantic advertise ``anyOf: [integer,
    number]`` unless the schema is pinned. Upstream declares a plain ``number``,
    and the generated TS type would widen for what is only an encoder detail.
    Nothing else would have caught this: the contract diff compares field
    presence, not JSON-schema types.
    """
    schema = _Model.model_json_schema(mode="serialization")
    assert schema["properties"]["value"]["type"] == "number", schema["properties"]["value"]
