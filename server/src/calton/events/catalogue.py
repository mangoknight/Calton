"""The event names upstream defines, transcribed from ``pkg/models/events.go``.

Names are a wire contract, not an internal detail: they are what a webhook
subscriber filters on, so a name that differs by one character is a subscriber
that silently never fires. They are therefore transcribed verbatim and pinned by
tests rather than derived from class names — deriving them is exactly how
`task.comment.edited` would become `task.comment.updated` (upstream's class is
``TaskCommentUpdatedEvent`` but its name says *edited*).

Two distinctions here are easy to erase by tidying, and both are asserted in
``tests/unit/test_event_catalogue.py``:

**Singular and plural are different events.** ``task.overdue`` (one task became
overdue) and ``tasks.overdue`` (the daily digest) are separate types upstream
(``events.go:210`` and ``:222``). Likewise ``task.created`` and
``tasks.batch.created``. Collapsing either pair looks like a typo fix.

**Not every event is a webhook event.** Upstream registers a subset for webhooks
(``webhooks.go:106-125``); the rest are internal. `project.created` is the sharp
edge: `project.updated`, `project.deleted` and both `project.shared.*` are
webhook-exposed, but *creation is not*. Measured against the Go server with
webhooks enabled, `GET /webhooks/events` returns exactly the 19 below.

⚠️ **The paragraph that used to sit here is out of date and the correction matters.**
It said Phase 1 does not expose `/webhooks/events`, does not deliver webhooks, and keeps
the capability flag off because "reporting `webhooks_enabled: true` while delivering
nothing would be a lie". Phase 2 changed two of those three:

* the four project-webhook routes and `GET /webhooks/events` are implemented, and the
  latter serves :data:`WEBHOOK_EVENTS` directly — so this list is now a wire contract in
  the most literal sense, not just the bus's vocabulary;
* `webhooks_enabled` is no longer a constant. It is derived from `webhooks.enabled`,
  exactly as upstream derives it, so it answers true or false according to how the server
  was started rather than according to a decision frozen in code.

**Delivery is still not implemented**, which is the part of the old reasoning that
survives: a subscriber can be created, listed and updated, and nothing will ever POST to
it. That gap is real and is not hidden by the flag — the flag reports whether the
capability is *switched on*, which is what upstream reports too.
"""

from __future__ import annotations

from typing import Final

#: Every event upstream defines, sorted. 37 of them.
ALL_EVENTS: Final[frozenset[str]] = frozenset(
    {
        # Fork-only admin surface; Calton does not implement it (design §5.3).
        "admin.access.denied",
        "admin.project.owner.changed",
        "admin.user.admin.granted",
        "admin.user.admin.revoked",
        "admin.user.created",
        "admin.user.deleted",
        "admin.user.password.set",
        "admin.user.status.changed",
        "admin.users.listed",
        "project.created",
        "project.deleted",
        "project.shared.team",
        "project.shared.user",
        "project.updated",
        "task.assignee.created",
        "task.assignee.deleted",
        "task.attachment.created",
        "task.attachment.deleted",
        "task.comment.created",
        "task.comment.deleted",
        "task.comment.edited",
        "task.created",
        "task.deleted",
        "task.overdue",
        "task.positions.recalculated",
        "task.relation.created",
        "task.relation.deleted",
        "task.reminder.fired",
        "task.updated",
        "tasks.batch.created",
        "tasks.overdue",
        "team.created",
        "team.deleted",
        "team.member.added",
        "team.member.removed",
        "user.export.requested",
        "webhook.delivery",
    }
)

#: The subset upstream exposes through ``GET /webhooks/events``, sorted as that
#: endpoint sorts them. Measured, not inferred: the Go server was run with
#: ``CALTON_WEBHOOKS_ENABLED=true`` and the response transcribed.
WEBHOOK_EVENTS: Final[tuple[str, ...]] = (
    "project.deleted",
    "project.shared.team",
    "project.shared.user",
    "project.updated",
    "task.assignee.created",
    "task.assignee.deleted",
    "task.attachment.created",
    "task.attachment.deleted",
    "task.comment.created",
    "task.comment.deleted",
    "task.comment.edited",
    "task.created",
    "task.deleted",
    "task.overdue",
    "task.relation.created",
    "task.relation.deleted",
    "task.reminder.fired",
    "task.updated",
    "tasks.overdue",
)

#: Events Phase 1 can actually raise. The admin surface is fork-only and webhook
#: delivery does not exist yet, so nothing may publish those; the bus refuses
#: them rather than letting a name typo look like a working publish.
PHASE1_EVENTS: Final[frozenset[str]] = (
    ALL_EVENTS
    - {name for name in ALL_EVENTS if name.startswith("admin.")}
    - {"webhook.delivery", "user.export.requested"}
)
