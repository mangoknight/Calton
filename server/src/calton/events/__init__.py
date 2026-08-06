"""Internal event bus (T35).

Phase 1 scope is the bus only. `GET /webhooks/events` and webhook delivery are
deliberately out: exposing the endpoint would require turning the capability flag
on, and reporting `webhooks_enabled: true` while delivering nothing is a lie to
clients rather than a documented difference from upstream.
"""

from calton.events.bus import (
    Event,
    EventBus,
    EventNotInPhase1Error,
    EventRecursionError,
    Listener,
    UnknownEventError,
)
from calton.events.catalogue import ALL_EVENTS, PHASE1_EVENTS, WEBHOOK_EVENTS

__all__ = [
    "ALL_EVENTS",
    "PHASE1_EVENTS",
    "WEBHOOK_EVENTS",
    "Event",
    "EventBus",
    "EventNotInPhase1Error",
    "EventRecursionError",
    "Listener",
    "UnknownEventError",
]
