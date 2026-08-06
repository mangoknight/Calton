"""T35 — the bus.

The two properties worth testing hard are the ones with a known bug behind them:

* **Listeners run in the caller's transaction.** Kanban writes `done`/`done_at`
  when a task moves into a done bucket and mirrors that into the project's other
  views. If those land in separate transactions, a mid-chain failure leaves a
  task done in one view and not in others — a state upstream cannot produce.

* **Reentrancy is guarded per subject, not globally.** Bucket and done are
  legitimately bidirectional. A guard that refused all nesting would break the
  feature it protects; one that allowed everything would loop forever.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.event import listen
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from calton.events import (
    Event,
    EventBus,
    EventNotInPhase1Error,
    EventRecursionError,
    UnknownEventError,
)
from calton.events.bus import MAX_DEPTH

Base = declarative_base()


class Note(Base):  # type: ignore[misc, valid-type]
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    body = Column(String)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# --- dispatch ----------------------------------------------------------------


def test_listeners_run_in_registration_order(bus: EventBus, session: Session) -> None:
    seen: list[str] = []
    bus.subscribe("task.created", lambda e, s: seen.append("first"))
    bus.subscribe("task.created", lambda e, s: seen.append("second"))

    bus.publish(Event("task.created", key=1), session)

    assert seen == ["first", "second"]


def test_publishing_with_no_listeners_is_fine(bus: EventBus, session: Session) -> None:
    assert bus.publish(Event("task.created", key=1), session) is True


def test_the_payload_reaches_the_listener(bus: EventBus, session: Session) -> None:
    received: list[Event] = []
    bus.subscribe("task.updated", lambda e, s: received.append(e))

    bus.publish(Event("task.updated", payload={"id": 7, "done": True}, key=7), session)

    assert received[0].payload == {"id": 7, "done": True}


# --- one transaction ---------------------------------------------------------


def test_a_listener_writes_in_the_callers_transaction(bus: EventBus, session: Session) -> None:
    """The write must be visible to the caller before anyone commits."""
    bus.subscribe("task.updated", lambda e, s: s.add(Note(id=1, body="from listener")))

    bus.publish(Event("task.updated", key=1), session)
    session.flush()

    assert session.get(Note, 1) is not None
    assert not session.in_nested_transaction()


def test_a_listener_write_rolls_back_with_the_caller(bus: EventBus, session: Session) -> None:
    """The property the kanban chain depends on. If the listener had committed on
    its own, this row would survive the caller's rollback and the task would be
    done in one view and not in others."""
    bus.subscribe("task.updated", lambda e, s: s.add(Note(id=2, body="doomed")))

    session.add(Note(id=3, body="caller"))
    bus.publish(Event("task.updated", key=1), session)
    session.rollback()

    assert session.get(Note, 2) is None
    assert session.get(Note, 3) is None


def test_the_bus_never_commits(bus: EventBus, session: Session) -> None:
    """Stated as its own assertion because "it happens not to commit today" and
    "it must not commit" are different guarantees.

    Observed through SQLAlchemy's own commit event rather than by inspecting
    rows: a listener that wrote nothing would make a row-based check pass without
    proving anything.
    """
    commits: list[str] = []
    listen(session, "after_commit", lambda s: commits.append("commit"))

    bus.subscribe("task.updated", lambda e, s: s.add(Note(id=4, body="written")))
    bus.publish(Event("task.updated", key=1), session)
    session.flush()

    assert session.get(Note, 4) is not None, "the listener must actually have written"
    assert commits == [], "the bus committed the caller's transaction"


def test_a_failing_listener_propagates_rather_than_being_swallowed(
    bus: EventBus, session: Session
) -> None:
    """A swallowed listener error is a half-applied write that reports success."""

    def explode(event: Event, s: Session) -> None:
        raise ValueError("listener failed")

    bus.subscribe("task.updated", explode)

    with pytest.raises(ValueError, match="listener failed"):
        bus.publish(Event("task.updated", key=1), session)


# --- reentrancy --------------------------------------------------------------


def test_the_done_bucket_ping_pong_terminates(bus: EventBus, session: Session) -> None:
    """The exact shape T28 produces: moving a task to the done bucket sets `done`,
    and setting `done` moves the bucket. Both listeners fire once and the chain
    stops, rather than recursing until the stack gives out."""
    calls: list[str] = []

    def on_bucket_change(event: Event, s: Session) -> None:
        calls.append("bucket")
        bus.publish(Event("task.updated", key=event.key), s)

    def on_task_updated(event: Event, s: Session) -> None:
        calls.append("done")
        bus.publish(Event("task.positions.recalculated", key=event.key), s)

    def on_positions(event: Event, s: Session) -> None:
        calls.append("positions")
        # Closing the loop: this is what would recurse forever unguarded.
        bus.publish(Event("task.updated", key=event.key), s)

    bus.subscribe("task.positions.recalculated", on_positions)
    bus.subscribe("task.updated", on_task_updated)
    bus.subscribe("task.created", on_bucket_change)

    bus.publish(Event("task.created", key=42), session)

    assert calls == ["bucket", "done", "positions"]


def test_the_guard_is_per_subject_not_global(bus: EventBus, session: Session) -> None:
    """A listener handling task 1 must still be able to raise the same event for
    task 2 — that is a sibling view being updated, not a loop. A global guard
    would drop it and the sibling would silently never sync."""
    handled: list[int] = []

    def on_update(event: Event, s: Session) -> None:
        handled.append(event.key)  # type: ignore[arg-type]
        if event.key == 1:
            bus.publish(Event("task.updated", key=2), s)

    bus.subscribe("task.updated", on_update)

    bus.publish(Event("task.updated", key=1), session)

    assert handled == [1, 2]


def test_re_publishing_the_same_subject_is_reported_as_suppressed(
    bus: EventBus, session: Session
) -> None:
    """`publish` returns False so a caller can tell "already in flight" from
    "nobody listened"; both would otherwise look like success."""
    results: list[bool] = []

    def on_update(event: Event, s: Session) -> None:
        results.append(bus.publish(Event("task.updated", key=event.key), s))

    bus.subscribe("task.updated", on_update)

    assert bus.publish(Event("task.updated", key=1), session) is True
    assert results == [False]


def test_the_guard_is_released_after_the_publish_completes(bus: EventBus, session: Session) -> None:
    """Suppression must last for the duration of one chain, not forever. A guard
    that leaked would make the second edit of a task silently do nothing."""
    calls: list[int] = []
    bus.subscribe("task.updated", lambda e, s: calls.append(1))

    bus.publish(Event("task.updated", key=1), session)
    bus.publish(Event("task.updated", key=1), session)

    assert calls == [1, 1]


def test_a_cycle_that_keeps_changing_subject_hits_the_depth_backstop(
    bus: EventBus, session: Session
) -> None:
    """The per-subject guard cannot catch a chain where every hop is a new
    subject, so MAX_DEPTH exists to fail loudly instead of exhausting the stack."""

    def forever(event: Event, s: Session) -> None:
        bus.publish(Event("task.updated", key=event.key + 1), s)  # type: ignore[operator]

    bus.subscribe("task.updated", forever)

    with pytest.raises(EventRecursionError, match=str(MAX_DEPTH)):
        bus.publish(Event("task.updated", key=0), session)


def test_the_depth_counter_is_restored_after_a_failure(bus: EventBus, session: Session) -> None:
    """A listener that raises must not leave the bus wedged at depth. Otherwise
    one failed request degrades every later one in the same process."""

    def explode(event: Event, s: Session) -> None:
        raise ValueError("boom")

    bus.subscribe("task.created", explode)
    for _ in range(MAX_DEPTH + 5):
        with pytest.raises(ValueError):
            bus.publish(Event("task.created", key=1), session)

    calls: list[int] = []
    bus.subscribe("task.updated", lambda e, s: calls.append(1))
    assert bus.publish(Event("task.updated", key=1), session) is True
    assert calls == [1]


# --- names -------------------------------------------------------------------


def test_an_unknown_event_name_is_refused(bus: EventBus, session: Session) -> None:
    """A typo that published nothing would be indistinguishable from a listener
    that never fires — the exact failure the catalogue exists to prevent."""
    with pytest.raises(UnknownEventError, match=re.escape("task.done")):
        bus.publish(Event("task.done", key=1), session)


def test_subscribing_to_an_unknown_name_is_refused_too(bus: EventBus) -> None:
    """Caught at wiring time rather than at the first publish, which may be a
    code path nothing exercises until production."""
    with pytest.raises(UnknownEventError):
        bus.subscribe("task.finished", lambda e, s: None)


def test_a_real_event_outside_phase_1_is_refused(bus: EventBus, session: Session) -> None:
    """`webhook.delivery` is a genuine upstream event, but Phase 1 does not
    deliver webhooks. Publishing it would announce a capability we do not have."""
    with pytest.raises(EventNotInPhase1Error, match=re.escape("webhook.delivery")):
        bus.publish(Event("webhook.delivery", key=1), session)


def test_the_admin_surface_is_refused(bus: EventBus, session: Session) -> None:
    with pytest.raises(EventNotInPhase1Error):
        bus.publish(Event("admin.user.created", key=1), session)
