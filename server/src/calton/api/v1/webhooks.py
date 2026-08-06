"""The four project-webhook routes.

All hand-written rather than mounted through ``CRUDRouter``: this resource has no item
read, its update writes one column while validating another, and the project segment in
the path does not scope the item lookup. Only the list and create halves would fit, and
splitting a four-route resource across two mechanisms buys nothing.

⚠️ **These routes are mounted only when ``webhooks.enabled`` is set** (the default, as
upstream). Upstream does not register them when the flag is off, and the parity harness
currently runs the Go side with it off while the MCP gate runs it on. Gating the mount
the same way is what lets Calton be right on both planes — see
``config.WebhooksSettings``.

⚠️ **The permission group is ``projects_webhooks``, not ``webhooks``.** The registry
derives it from the non-parameter segments, so this falls out automatically — but it is
worth stating, because the ``webhooks`` group really does exist and holds exactly one
entry (``GET /webhooks/events``, not implemented here). An earlier reading of that fact
concluded these four routes were unreachable by any API token; they are not, and the
mistake was assuming routes map one-to-one onto group names.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.core.crud_router import deleted_response, path_param_as_id
from calton.core.pagination import Paginator, paginated_response
from calton.db.session import get_db
from calton.models import User, Webhook
from calton.schemas.message import Message
from calton.schemas.user import UserRead
from calton.schemas.webhook import WebhookRead, WebhookWrite
from calton.services import webhook_service
from calton.services.team_service import user_id_of


def _creator(session: Session, created_by_id: int) -> UserRead | None:
    user = session.get(User, created_by_id)
    return UserRead.model_validate(user, from_attributes=True) if user is not None else None


def _view(session: Session, hook: Webhook) -> dict[str, object]:
    return webhook_service.webhook_view(
        session, hook, _creator(session, hook.created_by_id)
    ).model_dump(mode="json")


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project}/webhooks", response_model=list[WebhookRead])
    def read_webhooks(
        request: Request,
        # `str`, never `int` — an int annotation answers 422 where upstream answers
        # 400/2004. See core.crud_router.path_parameter_block.
        project: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        """A project's webhook targets.

        Read on the project is enough — not write, even though every other route here
        needs it. Refusal is 403 **code 1**, a different body from the write routes'
        code 0; both were measured.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)

        webhook_service.require_project_read(session, user_id, project_id)
        hooks = list(session.scalars(webhook_service.webhooks_of_project(project_id)))

        return paginated_response(
            [_view(session, hook) for hook in hooks],
            total_items=len(hooks),
            per_page=paginator.per_page,
            result_count=len(hooks),
        )

    @router.put("/projects/{project}/webhooks", status_code=201, response_model=WebhookRead)
    def create_webhook(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        body: WebhookWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Create one. The 201 masks the secret it just accepted."""
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)

        hook = webhook_service.create(session, user_id, project_id=project_id, body=body)
        payload = _view(session, hook)
        # `get_db` closes the session without committing, so a service that only flushes
        # has its work discarded after the response was already built and sent.
        session.commit()
        return JSONResponse(status_code=201, content=payload)

    @router.post(
        "/projects/{project}/webhooks/{webhook}",
        response_model=WebhookRead,
    )
    def update_webhook(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        webhook: Annotated[str, Path(min_length=1)],
        body: WebhookWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Change the events. ``target_url`` is required and then ignored.

        ⚠️ ``project`` is read only to satisfy the route; the webhook is found by id
        alone and the permission is checked against **its own** project. That is
        upstream's behaviour and it is measured — see ``webhook_service.load_for_write``.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        path_param_as_id(project)
        webhook_id = path_param_as_id(webhook)

        hook = webhook_service.update(session, user_id, webhook_id=webhook_id, body=body)
        # ⚠️ Not `_view`. The update response is the **request body** with project_id and
        # user_id overwritten from the stored row — it reports a target_url the database
        # did not keep. See webhook_service.updated_webhook_view.
        payload = webhook_service.updated_webhook_view(hook, body).model_dump(mode="json")
        session.commit()
        return JSONResponse(content=payload)

    @router.get("/webhooks/events", response_model=list[str])
    def read_webhook_events(request: Request) -> Response:
        """The 19 event names a webhook target may subscribe to.

        Authenticated but otherwise ungated — no project, no permission, the same list
        for everyone. It is a catalogue, not data.

        ⚠️ This one files under the group **``webhooks``**, not ``projects_webhooks``
        like the other four, because its path has a different first segment. That single
        entry is the whole of the ``webhooks`` group upstream, and mistaking it for the
        group the CRUD routes live in was what produced the (wrong) conclusion that no
        API token could reach them.

        The body is a bare JSON array of strings — not an object, not wrapped — and its
        order is upstream's own, which is why the names are re-exported from the event
        catalogue in that order rather than sorted here.
        """
        user_id_of(getattr(request.state, "auth", None))
        return JSONResponse(content=list(webhook_service.AVAILABLE_EVENTS))

    @router.delete("/projects/{project}/webhooks/{webhook}", response_model=Message)
    def delete_webhook(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        webhook: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """Remove one. A webhook that does not exist is **403**, not 404."""
        user_id = user_id_of(getattr(request.state, "auth", None))
        path_param_as_id(project)
        webhook_id = path_param_as_id(webhook)

        webhook_service.delete(session, user_id, webhook_id=webhook_id)
        session.commit()
        return deleted_response()

    return router


#: (method, path) for everything this module registers, so route_registry and the app can
#: never disagree about which routes exist.
#:
#: These file under **``projects_webhooks``** — three non-parameter segments joined with
#: an underscore — with the ordinary CRUD action names. Verified against the reference
#: server's own ``GET /routes``. Getting a group name wrong does not break routing; it
#: makes every API-token call against these routes 403 while JWT calls keep working.
REGISTERED_ROUTES = (
    # ⚠️ Under the group `webhooks` — the only member of it — while the four below are
    # `projects_webhooks`. Different first path segment, different group.
    ("GET", "/api/v1/webhooks/events"),
    ("GET", "/api/v1/projects/{project}/webhooks"),
    ("PUT", "/api/v1/projects/{project}/webhooks"),
    ("POST", "/api/v1/projects/{project}/webhooks/{webhook}"),
    ("DELETE", "/api/v1/projects/{project}/webhooks/{webhook}"),
)
