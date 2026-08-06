"""Go renders a zero ``time.Time`` as ``0001-01-01T00:00:00Z``, never as null and never
by omitting the key. Clients — the web UI and both MCP packages — read those fields
positionally and break on null, so the zero value has to survive the round trip.

Two behaviours exist upstream today, both in ``pkg/models/tasks.go``:

* ``json:"due_date"`` on a ``time.Time`` — always present, zero renders as the Go zero time.
* ``json:"deleted_at,omitzero"`` on a ``time.Time`` — key disappears when the value is zero.

``OptionalTimestamp`` covers a third shape, nil-pointer omission, which no upstream time
field currently uses. It is exercised here so it is known to work when Phase 2/3 needs it.
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ConfigDict

from calton.db.types import ZERO_TIME, OmitZeroTimestamp, OptionalTimestamp, Timestamp
from calton.schemas.base import CaltonModel


class Model(CaltonModel):
    due_date: Timestamp = ZERO_TIME
    deleted_at: OmitZeroTimestamp = ZERO_TIME
    done_at: OptionalTimestamp = None


def test_zero_datetime_serializes_to_go_zero_time() -> None:
    assert '"due_date":"0001-01-01T00:00:00Z"' in Model().model_dump_json()


def test_zero_datetime_round_trips() -> None:
    parsed = Model.model_validate_json('{"due_date":"0001-01-01T00:00:00Z"}')

    assert parsed.due_date == ZERO_TIME
    assert parsed.due_date.tzinfo is not None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 8, 3, 10, 30, tzinfo=UTC), "2026-08-03T10:30:00Z"),
        # Go's RFC3339Nano trims trailing zeros from the fractional part.
        (datetime(2026, 8, 3, 10, 30, 0, 123456, tzinfo=UTC), "2026-08-03T10:30:00.123456Z"),
        (datetime(2026, 8, 3, 10, 30, 0, 120000, tzinfo=UTC), "2026-08-03T10:30:00.12Z"),
        (datetime(2026, 8, 3, 10, 30, 0, 100000, tzinfo=UTC), "2026-08-03T10:30:00.1Z"),
    ],
)
def test_non_zero_datetimes_use_rfc3339(value: datetime, expected: str) -> None:
    assert f'"due_date":"{expected}"' in Model(due_date=value).model_dump_json()


def test_naive_datetimes_are_treated_as_utc() -> None:
    dumped = Model(due_date=datetime(2026, 8, 3, 10, 30)).model_dump_json()

    assert '"due_date":"2026-08-03T10:30:00Z"' in dumped


class TestOmission:
    def test_zero_omits_an_omitzero_field(self) -> None:
        assert "deleted_at" not in Model().model_dump_json()

    def test_non_zero_keeps_an_omitzero_field(self) -> None:
        dumped = Model(deleted_at=datetime(2026, 8, 3, tzinfo=UTC)).model_dump_json()

        assert '"deleted_at":"2026-08-03T00:00:00Z"' in dumped

    def test_none_omits_an_optional_field(self) -> None:
        assert "done_at" not in Model(done_at=None).model_dump_json()

    def test_zero_keeps_an_optional_field(self) -> None:
        assert '"done_at":"0001-01-01T00:00:00Z"' in Model(done_at=ZERO_TIME).model_dump_json()

    def test_null_is_never_emitted(self) -> None:
        assert "null" not in Model(done_at=None).model_dump_json()


class TestNullVersusMissing:
    """A missing key and an explicit null must stay distinguishable on the way in —
    ``parent_project_id``'s three-state handling (design R9) depends on it.
    """

    def test_missing_key_is_not_in_fields_set(self) -> None:
        parsed = Model.model_validate_json("{}")

        assert parsed.done_at is None
        assert "done_at" not in parsed.model_fields_set

    def test_explicit_null_is_in_fields_set(self) -> None:
        parsed = Model.model_validate_json('{"done_at":null}')

        assert parsed.done_at is None
        assert "done_at" in parsed.model_fields_set


class TestStrictModeAcceptsRfc3339:
    """Write schemas run under ``strict=True``, which refuses ``str -> datetime``.

    FastAPI validates request bodies in Python mode, where Pydantic's strict mode rejects
    an RFC3339 string outright. Every write carrying a date would 422 — including a client
    echoing back the ``"0001-01-01T00:00:00Z"`` it just read, which is precisely what both
    MCP clients do on read-modify-write, so this would have broken AC-3 wholesale.

    Go accepts RFC3339 on the way in, so accepting it is faithful rather than a
    relaxation. The conversion runs before the strict check, which leaves strictness
    intact for every other field.
    """

    class Write(CaltonModel):
        model_config = ConfigDict(
            strict=True, populate_by_name=True, extra="ignore", serialize_by_alias=True
        )

        title: str
        due_date: Timestamp = ZERO_TIME

    @pytest.fixture
    def client(self) -> TestClient:
        app = FastAPI()

        @app.post("/t")
        def write(body: TestStrictModeAcceptsRfc3339.Write) -> dict[str, str]:
            return {"due": body.due_date.isoformat()}

        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize(
        "sent",
        [
            "2026-08-03T10:30:00Z",
            "0001-01-01T00:00:00Z",
            "2026-08-03T10:30:00.123456Z",
            "2026-08-03T10:30:00+00:00",
            "2026-08-03T18:30:00+08:00",
        ],
    )
    def test_rfc3339_is_accepted(self, client: TestClient, sent: str) -> None:
        assert client.post("/t", json={"title": "x", "due_date": sent}).status_code == 200

    def test_the_zero_value_a_client_read_back_is_accepted(self, client: TestClient) -> None:
        """The read-modify-write case. A GET returns this exact string."""
        response = client.post("/t", json={"title": "x", "due_date": "0001-01-01T00:00:00Z"})

        assert response.status_code == 200
        assert response.json()["due"].startswith("0001-01-01")

    def test_an_offset_is_normalized_to_utc(self, client: TestClient) -> None:
        response = client.post("/t", json={"title": "x", "due_date": "2026-08-03T18:30:00+08:00"})

        assert response.json()["due"] == "2026-08-03T10:30:00+00:00"

    @pytest.mark.parametrize("sent", ["not a date", "", "2026-13-45T99:99:99Z", 12345, True])
    def test_rubbish_is_still_rejected(self, client: TestClient, sent: object) -> None:
        assert client.post("/t", json={"title": "x", "due_date": sent}).status_code == 422

    def test_strictness_is_preserved_for_other_fields(self, client: TestClient) -> None:
        """Only the datetime conversion is permitted; strict mode still applies elsewhere."""
        assert client.post("/t", json={"title": 42}).status_code == 422
