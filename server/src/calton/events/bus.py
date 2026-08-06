"""A synchronous, in-transaction event bus.

Three properties, each chosen because the alternative causes a specific bug we
already know about:

**Listeners run inside the caller's transaction.** ``publish`` takes the session
and never commits. Kanban is the reason: moving a task into a done bucket also
writes ``done``/``done_at`` (``kanban_task_bucket.go:137-159``) and mirrors the
done state into every other view of the project (``:193-215``). If a listener
committed on its own, a failure after the first write would leave the task done
in one view and not in others — a state upstream can never produce. One
transaction, one outcome.

**Dispatch is synchronous and ordered.** Not a queue, not a thread. The write is
not finished until its consequences are, because the response body has to reflect
them: a POST that moves a task to the done bucket returns the task with
``done: true`` already set.

**Re-entrant publishes are guarded per subject, not globally.** Done and bucket
are *legitimately* bidirectional — changing the bucket sets done, and setting done
moves the bucket — so a guard that refused all nesting would break the feature it
is meant to protect. The guard is keyed on ``(name, key)``: a listener may publish
anything about a *different* subject, but re-publishing the event already in
flight for the *same* subject is dropped. ``MAX_DEPTH`` is a backstop for a cycle
that walks between subjects forever.

Publishing an event name Phase 1 cannot raise is an error rather than a no-op. A
name is a wire contract (see catalogue.py), and a typo that silently publishes
nothing is indistinguishable from a listener that never fires.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy.orm import Session

from calton.events.catalogue import ALL_EVENTS, PHASE1_EVENTS

#: Backstop for a cycle that keeps changing subject so the per-subject guard
#: never trips. Deep enough for the real chains (bucket -> done -> sibling views
#: is 3) and shallow enough to fail before the stack does.
MAX_DEPTH: Final[int] = 16


class UnknownEventError(LookupError):
    """A name that is not in the catalogue at all — almost always a typo."""


class EventNotInPhase1Error(LookupError):
    """A real upstream event that Calton must not raise yet."""


class EventRecursionError(RuntimeError):
    """A publish chain exceeded MAX_DEPTH."""


@dataclass(frozen=True)
class Event:
    """One thing that happened.

    ``key`` identifies the *subject* — the task id, the project id. It is what
    makes the reentrancy guard per-subject rather than global, so leaving it None
    on an event that participates in a bidirectional chain reduces the guard to
    the depth backstop. Payload contents are the listener's business; the bus
    only reads the name and the key.
    """

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    key: Hashable | None = None


Listener = Callable[[Event, Session], None]


class EventBus:
    def __init__(self, allowed: frozenset[str] = PHASE1_EVENTS) -> None:
        self._listeners: dict[str, list[Listener]] = {}
        self._allowed = allowed
        self._in_flight: set[tuple[str, Hashable | None]] = set()
        self._depth = 0

    def subscribe(self, name: str, listener: Listener) -> None:
        """Register a listener. Order of registration is order of execution."""
        self._check_name(name)
        self._listeners.setdefault(name, []).append(listener)

    def listeners_for(self, name: str) -> tuple[Listener, ...]:
        return tuple(self._listeners.get(name, ()))

    def publish(self, event: Event, session: Session) -> bool:
        """Run every listener for this event, in the caller's transaction.

        Returns False when the publish was suppressed by the reentrancy guard,
        so a caller that cares can tell "nobody listened" from "we were already
        handling this". Never commits and never rolls back: the caller owns the
        transaction boundary.
        """
        self._check_name(event.name)

        subject = (event.name, event.key)
        if subject in self._in_flight:
            return False

        with self._entered(subject):
            for listener in self.listeners_for(event.name):
                listener(event, session)
        return True

    @contextmanager
    def _entered(self, subject: tuple[str, Hashable | None]) -> Iterator[None]:
        if self._depth >= MAX_DEPTH:
            raise EventRecursionError(
                f"event chain exceeded {MAX_DEPTH} levels at {subject[0]!r}; "
                "a listener is publishing an ever-changing subject in a cycle"
            )
        self._in_flight.add(subject)
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            self._in_flight.discard(subject)

    def _check_name(self, name: str) -> None:
        if name not in ALL_EVENTS:
            raise UnknownEventError(
                f"{name!r} is not an event upstream defines. Names are transcribed "
                "verbatim in events/catalogue.py; check the spelling against "
                "pkg/models/events.go rather than inventing one."
            )
        if name not in self._allowed:
            raise EventNotInPhase1Error(
                f"{name!r} exists upstream but Phase 1 must not raise it "
                "(fork-only admin surface, or webhook delivery which is not built)"
            )
