"""The four project-webhook operations.

Three upstream behaviours drive every decision in this module, and none of them is what
the endpoint shape suggests. All measured against the reference service running on
upstream's default config (``webhooks.enabled`` true), which is **not** the plane the
parity harness currently uses — see ``config.WebhooksSettings``.

**1. The update writes one column.** ``Webhook.Update`` is
``s.Where("id = ?", w.ID).Cols("events").Update(w)`` (webhooks.go:298). ``target_url`` is
*required* by the validator on that same request and then discarded; ``secret`` and the
basic-auth pair likewise. Measured end to end:

    before                                       [(9, 1, '.../a', ['task.created'])]
    POST {target_url: '.../CHANGED', events: [...]}  -> 200
    after                                        [(9, 1, '.../a', ['task.deleted'])]

I first read the unchanged ``target_url`` as "the update did not apply at all". It was
the ``events`` column that disproved it — a reminder that observing only the field you
sent is too narrow a view to conclude from.

**2. The project in the path does not scope the update.** That same WHERE has no project
clause, and ``canDoWebhook`` re-loads the webhook to check rights against **its own**
project (webhooks_permissions.go:47). So ``POST /projects/2/webhooks/9`` succeeds for a
webhook that lives on project 1, as long as the caller may write project 1 — even when
they have no rights at all on project 2. Verified not to be exploitable: a caller with
rights only on their *own* project is refused (403) and the victim's row is untouched, so
this is odd rather than dangerous, and it is copied rather than tightened.

**3. Write-only fields are masked on the way out**, including on the 201 that accepted
them.
"""

from __future__ import annotations

import json

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError, ValidationError
from calton.core.policy import ForbiddenError
from calton.db.base import utcnow
from calton.events.catalogue import WEBHOOK_EVENTS
from calton.models import Webhook
from calton.permissions import project as project_permissions
from calton.schemas.user import UserRead
from calton.schemas.webhook import WebhookRead, WebhookWrite
from calton.services.project_crud import load_project

#: The event names ``GET /webhooks/events`` serves, and the set an unknown name is
#: checked against.
#:
#: ⚠️ **Re-exported, not re-listed.** ``events.catalogue.WEBHOOK_EVENTS`` already held
#: these 19 names, transcribed from the Go source and pinned by
#: ``tests/unit/test_event_catalogue.py``; this module briefly carried a second copy of
#: the same literal, measured independently and identical. Two hand-maintained copies of
#: a wire contract is the defect I had just written up about the endpoint count living in
#: three files — a name that drifts in one copy is a webhook subscriber that silently
#: never fires.
AVAILABLE_EVENTS: tuple[str, ...] = WEBHOOK_EVENTS


def require_project_read(session: Session, user_id: int, project_id: int) -> None:
    """The list route's gate: read on the project.

    Two different refusals, both measured and both from a different layer upstream:
    a project that does not exist is **404/3001** (it is looked up before anything else),
    and one the caller cannot see is **403 code 1** — ``"You're not allowed to do this."``,
    not the CRUD pipeline's code-0 ``"Forbidden"``. The write routes below use the other
    one, so the two must not be shared.
    """
    load_project(session, project_id)
    if not project_permissions.can_read(session, user_id, project_id)[0]:
        raise CaltonError.from_name("models.ErrGenericForbidden")


def _require_write(session: Session, user_id: int, project_id: int) -> None:
    """The write routes' gate: write on the project, refused as **403 code 0**.

    Note the project lookup comes first here too, so ``PUT /projects/99999/webhooks``
    is 404/3001 rather than a 403 — the same order the list route uses.
    """
    load_project(session, project_id)
    if not project_permissions.can_write(session, user_id, project_id):
        raise ForbiddenError()


def webhooks_of_project(project_id: int) -> Select[tuple[Webhook]]:
    """A project's webhooks, id ascending.

    Filtered on ``project_id`` rather than on "``user_id`` is null": one table holds both
    project and user webhooks and nothing enforces that they are exclusive.
    """
    return select(Webhook).where(Webhook.project_id == project_id).order_by(Webhook.id)


def load_for_write(session: Session, webhook_id: int) -> Webhook:
    """The webhook, whichever project the caller named.

    ⚠️ Deliberately does **not** filter on the project from the path. See point 2 in the
    module docstring: upstream identifies the row by id alone and checks permission
    against the row's own project. Adding the filter here would be the tidy reading and
    would turn a measured 200 into a 403.
    """
    webhook = session.get(Webhook, webhook_id)
    if webhook is None:
        # No 404 on this path: upstream's permission check loads the row too, so a
        # missing webhook is refused before anything reports it missing. Measured —
        # DELETE of an absent or already-deleted webhook is 403, not 404.
        raise ForbiddenError()
    return webhook


def _validate_events(events: list[str]) -> None:
    """Unknown event names answer 412 with a **bare field name** and no message.

    ``["events"]``, not ``["events: ..."]``: upstream builds this one with
    ``InvalidFieldError([]string{"events"})`` rather than through the validator, so there
    is no tag text to render after the colon. Calton spells that class ``ValidationError``.
    """
    if any(event not in AVAILABLE_EVENTS for event in events):
        raise ValidationError(["events"])


def create(session: Session, user_id: int, *, project_id: int, body: WebhookWrite) -> Webhook:
    _require_write(session, user_id, project_id)
    _validate_events(body.events)

    now = utcnow()
    webhook = Webhook(
        target_url=body.target_url,
        events=json.dumps(body.events),
        project_id=project_id,
        user_id=0,
        secret=body.secret,
        basic_auth_user=body.basic_auth_user,
        basic_auth_password=body.basic_auth_password,
        created_by_id=user_id,
        created=now,
        updated=now,
    )
    session.add(webhook)
    session.flush()
    return webhook


def update(session: Session, user_id: int, *, webhook_id: int, body: WebhookWrite) -> Webhook:
    """Writes ``events`` and nothing else — see point 1 in the module docstring."""
    webhook = load_for_write(session, webhook_id)
    _require_write(session, user_id, webhook.project_id or 0)
    _validate_events(body.events)

    # ⚠️ target_url, secret and the basic-auth pair are NOT written, even though
    # target_url was just required by the schema. That asymmetry is upstream's; writing
    # them here is the obvious "fix" and is a divergence on every update.
    webhook.events = json.dumps(body.events)
    webhook.updated = utcnow()
    session.flush()
    return webhook


def delete(session: Session, user_id: int, *, webhook_id: int) -> None:
    webhook = load_for_write(session, webhook_id)
    _require_write(session, user_id, webhook.project_id or 0)
    session.delete(webhook)
    session.flush()


def updated_webhook_view(webhook: Webhook, body: WebhookWrite) -> WebhookRead:
    """The ``POST`` response, which is **the request body**, not the stored row.

    Upstream serialises the struct it bound rather than re-reading, so the client is
    handed back the ``target_url`` it just sent — **the one the database did not keep**.
    Alongside it: ``created`` is the zero time and ``created_by`` is null, because nothing
    ever loaded them onto that struct.

    Two fields are exceptions, and they are the tell that this is not a pure echo:
    ``project_id`` and ``user_id`` come from the stored row. ``canDoWebhook`` assigns them
    onto the bound struct while checking rights (webhooks_permissions.go:63-64), which is
    why ``POST /projects/2/webhooks/9`` answers ``project_id: 1`` — the path said 2 and
    the row said 1, and the row wins.

    So the response reports a ``target_url`` that was not saved and a ``project_id`` that
    was. Returning the stored row instead is the obvious implementation and diverges on
    three fields at once; returning a pure echo of the body diverges on ``project_id``.
    """
    return WebhookRead(
        id=webhook.id,
        project_id=webhook.project_id or 0,
        user_id=webhook.user_id or 0,
        target_url=body.target_url,
        events=list(body.events),
        secret="",
        basic_auth_user="",
        basic_auth_password="",
        # Echoed from the request, not read from the row — see WebhookWrite. An RMW
        # client gets its own values back; a minimal body gets the zero time and null.
        created_by=(
            UserRead(
                id=body.created_by.id,
                name=body.created_by.name,
                username=body.created_by.username,
                created=body.created_by.created,
                updated=body.created_by.updated,
            )
            if body.created_by is not None
            else None
        ),
        created=body.created,
        updated=webhook.updated,
    )


def webhook_view(session: Session, webhook: Webhook, creator: UserRead | None) -> WebhookRead:
    """The response body, with the write-only fields masked.

    ``secret`` and the basic-auth pair come back as ``""`` on every route including the
    201 — the values are stored and never readable again.
    """
    return WebhookRead(
        id=webhook.id,
        project_id=webhook.project_id or 0,
        user_id=webhook.user_id or 0,
        target_url=webhook.target_url,
        events=json.loads(webhook.events) if webhook.events else [],
        secret="",
        basic_auth_user="",
        basic_auth_password="",
        created_by=creator,
        created=webhook.created,
        updated=webhook.updated,
    )
