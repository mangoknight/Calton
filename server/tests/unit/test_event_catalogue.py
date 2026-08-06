"""T35 — the event names are a wire contract, so they are pinned one by one.

A webhook subscriber filters on the literal name. A name that differs by one
character is not a bug that shows up as an error; it is a subscriber that never
fires, discovered by a customer months later. So these read like transcription
checks, because that is what they are.

The pairs below are the ones a rewrite erases by accident. Each is asserted
separately rather than as part of a set comparison, so the failure names the
specific distinction that was lost instead of printing a 37-element diff.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from calton.events import ALL_EVENTS, PHASE1_EVENTS, WEBHOOK_EVENTS

REPO_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_EVENTS = REPO_ROOT / "pkg" / "models" / "events.go"


def _upstream_names() -> set[str]:
    """Names as they appear in events.go. Every Name() body is `return "<name>"`."""
    if not UPSTREAM_EVENTS.is_file():
        pytest.skip(f"{UPSTREAM_EVENTS} not present")
    return set(re.findall(r'return "([a-z][a-z.]*\.[a-z.]+)"', UPSTREAM_EVENTS.read_text()))


class TestSingularAndPluralAreDifferentEvents:
    """The distinction most likely to be "fixed" by a tidy-minded rewrite."""

    def test_both_overdue_events_exist(self) -> None:
        """`task.overdue` fires for one task becoming overdue; `tasks.overdue` is
        the digest covering many. Two types upstream (events.go:210 and :222),
        two names, both webhook-exposed. Collapsing them silently drops one of
        the two notifications a user relies on."""
        assert "task.overdue" in ALL_EVENTS
        assert "tasks.overdue" in ALL_EVENTS
        assert "task.overdue" in WEBHOOK_EVENTS
        assert "tasks.overdue" in WEBHOOK_EVENTS

    def test_task_created_and_batch_created_are_not_the_same(self) -> None:
        """Same trap, and here the asymmetry is sharper: `task.created` is
        webhook-exposed and `tasks.batch.created` is not."""
        assert "task.created" in ALL_EVENTS
        assert "tasks.batch.created" in ALL_EVENTS
        assert "task.created" in WEBHOOK_EVENTS
        assert "tasks.batch.created" not in WEBHOOK_EVENTS


class TestNamesThatDoNotFollowFromTheirType:
    def test_comment_update_is_called_edited(self) -> None:
        """Upstream's type is `TaskCommentUpdatedEvent`, its name is
        `task.comment.edited`. Deriving names from class names — the obvious
        implementation — gets this one wrong and nothing else catches it."""
        assert "task.comment.edited" in ALL_EVENTS
        assert "task.comment.updated" not in ALL_EVENTS

    def test_task_update_is_called_updated(self) -> None:
        """The counter-example that makes the previous one a real distinction
        rather than a global rule: tasks use `updated`, comments use `edited`."""
        assert "task.updated" in ALL_EVENTS
        assert "task.edited" not in ALL_EVENTS


class TestNotEveryEventIsAWebhookEvent:
    def test_project_creation_is_not_webhook_exposed(self) -> None:
        """The sharp edge. Every other project lifecycle event is exposed —
        updated, deleted, both shares — but creation is not. It looks like an
        omission and is not: a webhook is configured *on a project*, so there is
        nowhere for a project-creation hook to be registered."""
        assert "project.created" in ALL_EVENTS
        assert "project.created" not in WEBHOOK_EVENTS
        for exposed in ("project.updated", "project.deleted", "project.shared.user"):
            assert exposed in WEBHOOK_EVENTS

    def test_the_exposed_list_is_exactly_nineteen_and_sorted(self) -> None:
        """Measured against the Go server with CALTON_WEBHOOKS_ENABLED=true.
        `GetAvailableWebhookEvents` sorts before returning (webhooks.go:117-125),
        so order is part of the response body and therefore part of the contract."""
        assert len(WEBHOOK_EVENTS) == 19
        assert list(WEBHOOK_EVENTS) == sorted(WEBHOOK_EVENTS)

    def test_every_webhook_event_is_a_real_event(self) -> None:
        assert set(WEBHOOK_EVENTS) <= ALL_EVENTS


class TestPhase1Scope:
    def test_the_admin_surface_is_excluded(self) -> None:
        """Fork-only (design §5.3). Listed in the catalogue so the count matches
        upstream, excluded from what Phase 1 may publish."""
        admin = {name for name in ALL_EVENTS if name.startswith("admin.")}
        assert len(admin) == 9
        assert not admin & PHASE1_EVENTS

    def test_webhook_delivery_is_excluded_because_delivery_does_not_exist(self) -> None:
        assert "webhook.delivery" in ALL_EVENTS
        assert "webhook.delivery" not in PHASE1_EVENTS

    def test_the_kanban_events_are_in_scope(self) -> None:
        """T28 publishes these; if they were excluded the bus would refuse them
        at runtime rather than at import."""
        for name in ("task.updated", "task.positions.recalculated"):
            assert name in PHASE1_EVENTS


class TestTheCatalogueMatchesUpstream:
    """The transcription itself. Everything above is worthless if the source moved."""

    def test_no_event_upstream_defines_is_missing(self) -> None:
        missing = sorted(_upstream_names() - ALL_EVENTS)

        assert not missing, (
            f"events.go defines names the catalogue does not have: {missing}. "
            "Upstream has added events; transcribe them verbatim."
        )

    def test_the_catalogue_invents_nothing(self) -> None:
        invented = sorted(ALL_EVENTS - _upstream_names())

        assert not invented, (
            f"the catalogue has names events.go does not define: {invented}. "
            "Either upstream removed them or one is a typo — a typo here is a "
            "subscriber that never fires."
        )

    def test_the_count_is_stated_so_a_silent_shift_is_visible(self) -> None:
        assert len(ALL_EVENTS) == 37
