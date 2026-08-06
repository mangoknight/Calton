"""Read-modify-write must survive a round trip — the assertion that guards the MCP client.

A real MCP client updates by **echoing back what it read**: `GET` the whole task, change a
field or two, `POST` the entire object. So everything we put in a read response comes back
to us as a request body, and — this is the part that bites — straight back out again,
because ``task_service._write_view`` assigns the request's ``labels`` / ``attachments``
onto the *response* model (``task_service.py``).

That makes the response model, not the request model, the thing that has to tolerate
whatever a client echoes.

**Why this file exists as its own test rather than a line in an existing one.** The 7 red
``test_implemented_operations_match_the_contract`` cases complain that ``labels`` and
``attachments`` serialise as ``array<?>`` where the contract wants ``array<object>``,
because they are typed ``list[Any]``. The one-line fix — type them ``list[LabelRead]`` /
``list[AttachmentRead]`` — makes the contract diff green and turns **every** read-modify-
write into a **500**. Measured against a scratch copy before anything was decided:

======================================  ========  ==================
request                                 as-is     after tightening
======================================  ========  ==================
``POST`` the ``GET`` body back, byte for byte  200       **500**
a label carrying an extra key                  200       **500**
a label carrying only ``id``                   200       **500**
======================================  ========  ==================

Three things make it worse than a normal regression:

1. **The trigger is not a malformed request.** An unmodified ``GET`` → ``POST`` round trip
   is enough. There is nothing for a client to do differently.
2. **Our own frontend cannot reach it**, because it sends only the fields it cares about.
   Only a client that echoes back what it read walks into it — which is precisely the MCP
   client, and precisely what the acceptance line is for.
3. **The write has already happened when the response blows up.** The task *is* updated
   and the caller sees a 500, so a retry does it again. A "failed" request that actually
   succeeded is worse than one that plainly failed.

The rule in the briefing — *write schemas must be* ``extra="ignore"`` *and must not add
validation to read-only fields* — is correct and does **not** cover this: ``TaskRead`` is
not a write schema, and the damage is done by FastAPI validating the **response**.
Following that rule to the letter leaves you exposed, which is why this needed its own
assertion rather than trusting the convention.

⚠️ **These tests are the acceptance criterion for changing how write responses are
serialised, and they are deliberately in place before that change.** Without them, every
candidate fix looks identical to the suite. Splitting the read and write serialisers is
the real repair (coder-d's structural item); when it lands, these must still pass. If they
start failing, the fix has re-introduced the 500 — do not relax them.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.main import create_app
from calton.models.label import Label, LabelTask
from calton.models.project import Project
from calton.models.task import Task
from calton.models.task_assignee import TaskAssignee
from calton.models.task_reminder import TaskReminder
from calton.models.user import User

ALICE = 900
BOB = 901
PROJECT = 950
TASK = 950


@pytest.fixture
def rmw_engine() -> Iterator[Engine]:
    """A task with **labels and an assignee actually attached**.

    Not incidental. An empty collection and an unimplemented one serialise to the same
    JSON, so a task with nothing attached passes this test no matter how ``labels`` is
    typed — pydantic never has to coerce an item because there are no items. The whole
    failure lives in coercing echoed *elements*, so the fixture must supply some.
    """
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as session:
        session.add_all(
            [
                User(id=ALICE, username="alice"),
                User(id=BOB, username="bob"),
                Project(id=PROJECT, title="p", owner_id=ALICE),
                Task(id=TASK, title="t", project_id=PROJECT, index=1, created_by_id=ALICE),
                Label(id=950, title="X-alpha", created_by_id=ALICE),
                Label(id=951, title="X-beta", created_by_id=ALICE),
                LabelTask(id=1, task_id=TASK, label_id=950),
                LabelTask(id=2, task_id=TASK, label_id=951),
                # ⚠️ Two reminders, and they are not decoration. Until they were added
                # the fixture carried none, so `reminders` serialised as `null` on the
                # read and the round trip never put one in front of the request parser —
                # every assertion below was a fixed point for that field. Two rather than
                # one so the echo cannot pass by collapsing the list.
                TaskReminder(id=1, task_id=TASK, reminder=datetime(2026, 5, 1, tzinfo=UTC)),
                TaskReminder(id=2, task_id=TASK, reminder=datetime(2026, 8, 1, tzinfo=UTC)),
                TaskAssignee(id=1, task_id=TASK, user_id=BOB),
            ]
        )
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def rmw_client(rmw_engine: Engine) -> TestClient:
    app: FastAPI = create_app(engine=rmw_engine)
    app.state.session_factory = sessionmaker(
        bind=rmw_engine, class_=Session, expire_on_commit=False
    )

    from calton.auth.deps import get_auth_subject

    # The subject is stubbed the way the other unit suites do it; this file is about
    # response serialisation, not about the auth chain (test_api_tokens covers that).
    class _Subject:
        id = ALICE

    app.dependency_overrides[get_auth_subject] = lambda: _Subject()

    @app.middleware("http")
    async def _attach(request: Any, call_next: Any) -> Any:
        request.state.auth = _Subject()
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=False)


def _read(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/tasks/{TASK}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    # Guards the fixture, not the behaviour: if these ever come back empty the three
    # tests below become vacuous and would keep passing through the regression.
    assert body["labels"], "fixture must attach labels or these tests assert nothing"
    assert body["assignees"], "fixture must attach an assignee or these tests are weaker"
    return body


def test_echoing_the_read_body_back_unmodified_is_200(rmw_client: TestClient) -> None:
    """The plain round trip. Not one byte is changed between the GET and the POST."""
    body = _read(rmw_client)

    response = rmw_client.post(f"/api/v1/tasks/{TASK}", json=body)

    assert response.status_code == 200, (
        f"a read-modify-write round trip answered {response.status_code}. Typing "
        f"TaskRead.labels/attachments as a concrete model does this — see the module "
        f"docstring. Body: {response.text[:400]}"
    )


def test_a_label_carrying_an_unknown_key_is_200(rmw_client: TestClient) -> None:
    """An older or newer client, or one that decorates what it read.

    Separate from the plain round trip because it fails for a different reason: the extra
    key survives ``extra="ignore"`` on the way *in* and then has to survive serialisation
    on the way *out*.
    """
    body = _read(rmw_client)
    body["labels"] = [{"id": 950, "title": "X-alpha", "unknown_field": "zzz"}]

    response = rmw_client.post(f"/api/v1/tasks/{TASK}", json=body)

    assert response.status_code == 200, response.text[:400]


def test_a_label_carrying_only_an_id_is_200(rmw_client: TestClient) -> None:
    """The other direction: a client that sends less than it read.

    Both directions are asserted on purpose. A fix that coerces echoed items into a model
    with defaults would pass the "only an id" case and still fail the "unknown key" one,
    and a fix that allows extra keys but requires the full set does the reverse.
    """
    body = _read(rmw_client)
    body["labels"] = [{"id": 950}]

    response = rmw_client.post(f"/api/v1/tasks/{TASK}", json=body)

    assert response.status_code == 200, response.text[:400]


def test_echoing_the_read_reminders_back_is_200_and_keeps_them(
    rmw_client: TestClient,
) -> None:
    """★ The reverse case for the tightened request side.

    ``TaskWrite.reminders`` now takes objects rather than ``Any``, so that a bad element is
    refused while binding instead of while serialising — upstream refuses it there too, and
    refuses it *before* writing anything. Tightening a request field is exactly the move
    the round-trip lens exists to question: the client that walks into it is the one that
    posts back what it read, and our own frontend never does.

    So this asserts the shape a real client sends: the whole ``reminders`` array, straight
    from the read, comes back 200 **and the rows survive**. Both halves matter — omitting
    the key deletes them (measured, and pinned in the corpus), so a 200 alone would not
    show that echoing preserves them.
    """
    body = _read(rmw_client)
    assert body["reminders"], "premise: the fixture task must carry reminders"

    response = rmw_client.post(f"/api/v1/tasks/{TASK}", json=body)

    assert response.status_code == 200, response.text[:400]
    assert response.json()["reminders"] == body["reminders"]
    assert _read(rmw_client)["reminders"] == body["reminders"]


@pytest.mark.parametrize("field", ["labels", "reminders"])
def test_a_non_object_element_is_refused_at_bind_time_and_writes_nothing(
    rmw_client: TestClient, field: str
) -> None:
    """The other side of the same coin: what the tightening is *for*.

    A string where an object belongs is 400/2004 upstream, and — the part that decides
    where the check belongs — **nothing is written**. Measured on the reference service for
    both fields, on create and on update.

    Rejecting it on the way out instead would answer 500 *after* the row had changed, which
    is the failure this file was written to prevent. Asserting the title afterwards is what
    separates "refused" from "refused after doing it".
    """
    before = _read(rmw_client)
    body = dict(before)
    body["title"] = "should not be stored"
    body[field] = ["not an object"]

    response = rmw_client.post(f"/api/v1/tasks/{TASK}", json=body)

    assert response.status_code == 400
    assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}
    assert _read(rmw_client)["title"] == before["title"]
