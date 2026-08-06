"""The project write schema, checked against responses recorded from the Go server.

``tests/fixtures/go_project_contract.json`` holds what the reference server actually did:
the validation cases with the body that produced them, one successful create, and a full
read-modify-write echo. Every case carries its ``request_body``, because the case that
matters most is defined by what is *sent* rather than by what comes back.

The read-modify-write case is a tripwire, not a demonstration. It passes today for a
reason that is easy to undo: ``ProjectWrite`` does not declare ``views``, so the echoed
array is dropped by ``extra="ignore"`` before validation. The moment somebody adds a
``views`` field, the nested ``view_kind`` strings are parsed under ``strict=True`` and
every MCP update becomes a 422. This test is the only thing that would notice, so do not
remove it for being reliably green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from calton.schemas.project import ProjectWrite

_CONTRACT = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "go_project_contract.json").read_text()
)


def _validation_case(name: str) -> dict[str, Any]:
    for case in _CONTRACT["validation"]:
        if case["case"] == name:
            return dict(case)
    raise AssertionError(f"no recorded case named {name!r}")


class TestRecordedValidationCases:
    def test_an_empty_title_is_rejected(self) -> None:
        case = _validation_case("empty_title")
        assert case["http_status"] == 412
        assert case["response"]["code"] == 2002

        with pytest.raises(ValidationError):
            ProjectWrite.model_validate(case["request_body"])

    def test_a_missing_title_is_rejected(self) -> None:
        case = _validation_case("missing_title")
        assert case["http_status"] == 412

        with pytest.raises(ValidationError):
            ProjectWrite.model_validate(case["request_body"])

    def test_a_whitespace_only_title_is_accepted(self) -> None:
        """Measured 201. ``required`` means non-zero, not non-blank.

        A ``strip()`` in this schema would reject a body the reference server accepts,
        and nothing local would catch it — the parity harness would.
        """
        case = _validation_case("whitespace_title")
        assert case["http_status"] == 201

        written = ProjectWrite.model_validate(case["request_body"])

        assert written.title == "   "

    def test_the_recorded_create_answers_201(self) -> None:
        """PUT creates, and creation is 201 rather than 200."""
        assert _CONTRACT["created"]["http_status"] == 201

    def test_a_hash_prefixed_colour_is_stored_without_it(self) -> None:
        """The server normalises ``#ff0000`` to ``ff0000`` before storing it."""
        case = _validation_case("hash_prefixed_hex_color")

        assert case["request_body"]["hex_color"] == "#ff0000"
        assert case["response"]["hex_color"] == "ff0000"


class TestReadModifyWriteEcho:
    """Card item ⑤: posting a whole project back must not 422."""

    def test_the_recorded_echo_was_accepted(self) -> None:
        assert _CONTRACT["rmw_echo"]["http_status"] == 200

    def test_the_recorded_request_carried_views_with_string_kinds(self) -> None:
        """Guards the fixture itself: without this the case below proves nothing.

        If a regeneration ever recorded a body with no ``views``, the acceptance test
        would still pass while no longer exercising the thing it exists for.
        """
        body = _CONTRACT["rmw_echo"]["request_body"]

        assert "views" in body
        assert len(body["views"]) == 4
        assert [view["view_kind"] for view in body["views"]] == [
            "list",
            "gantt",
            "table",
            "kanban",
        ]

    def test_echoing_the_whole_object_back_validates(self) -> None:
        written = ProjectWrite.model_validate(_CONTRACT["rmw_echo"]["request_body"])

        assert written.title == _CONTRACT["rmw_echo"]["request_body"]["title"]

    def test_the_views_array_is_dropped_rather_than_parsed(self) -> None:
        """The mechanism that makes the above work, asserted directly.

        Declaring ``views`` on ``ProjectWrite`` would parse the nested string enums under
        strict mode and 422. Nothing else in the suite would report that.
        """
        assert "views" not in ProjectWrite.model_fields

        written = ProjectWrite.model_validate(_CONTRACT["rmw_echo"]["request_body"])

        assert not hasattr(written, "views")

    def test_read_only_fields_in_the_echo_are_ignored_not_rejected(self) -> None:
        """``owner``, ``max_permission`` and the timestamps all come back in the body."""
        body = _CONTRACT["rmw_echo"]["request_body"]
        assert {"owner", "max_permission", "created", "updated"} <= body.keys()

        written = ProjectWrite.model_validate(body)

        assert not hasattr(written, "owner")
        assert not hasattr(written, "max_permission")


class TestStrictnessIsNotWeakened:
    """The schema accepts Go's conversions and nothing else."""

    def test_a_numeric_string_is_still_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProjectWrite.model_validate({"title": "t", "position": "5"})

    def test_a_string_boolean_is_still_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProjectWrite.model_validate({"title": "t", "is_archived": "yes"})

    def test_an_explicit_null_parent_is_accepted_and_means_not_sent(self) -> None:
        written = ProjectWrite.model_validate({"title": "t", "parent_project_id": None})

        assert written.parent_project_id is None
