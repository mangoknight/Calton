"""The notification routes.

``GET /notifications`` lists the caller's own, and the collection-level
``POST /notifications`` marks them all read. Both file under the ``notifications``
group and are token-grantable.

⚠️ **The per-notification toggle is JWT-only and deliberately unregistered.**
``POST /notifications/{id}`` is absent from upstream's API-token route registry —
measured 401/11 with every notification permission granted — so no token client can
reach it. The route is mounted here for JWT callers but **not** added to
``REGISTERED_ROUTES``, which the API-token middleware reads to decide whether a path is
authorisable at all: a path absent from the registry is refused to every token, so the
route stays JWT-only without a separate exclusion list to keep in sync.

⚠️ The collection POST files under the action ``mark_all_as_read`` rather than a name of
its own: it is a POST on a collection path, and ``notifications`` is in the registry's
CRUD resource set, so the generic mapping applies. Upstream's own ``GET /routes``
agrees.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.core.crud_router import path_param_as_id
from calton.core.pagination import Paginator, paginated_response
from calton.db.session import get_db
from calton.schemas.message import Message
from calton.schemas.notification import NotificationMarkRead, NotificationRead
from calton.services import notification_service
from calton.services.team_service import user_id_of


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/notifications", response_model=list[NotificationRead])
    def read_notifications(
        request: Request,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        """The caller's own notifications, newest first.

        No permission question is asked beyond authentication: the query is scoped by
        ``notifiable_id``, so "may I list these" is not a thing upstream checks. An empty
        result is ``[]`` with a zero result-count and **zero** total-pages.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))

        query = notification_service.own_notifications_query(user_id)
        total = notification_service.count_own(session, user_id)
        if paginator.per_page > 0:
            query = query.limit(paginator.per_page).offset(
                (max(paginator.page, 1) - 1) * paginator.per_page
            )
        rows = list(session.scalars(query))

        return paginated_response(
            [notification_service.notification_view(row).model_dump(mode="json") for row in rows],
            total_items=total,
            per_page=paginator.per_page,
            result_count=len(rows),
        )

    @router.post("/notifications", response_model=Message)
    def mark_all_notifications_read(
        request: Request,
        session: Session = Depends(get_db),
    ) -> Response:
        """Mark every one of the caller's notifications read.

        Answers ``{"message": "success"}`` — **not** the generic delete message and not
        the resource. A caller with no notifications at all still gets it, and so does a
        second call, which rewrites ``read_at`` rather than skipping already-read rows.

        Any request body is ignored; upstream binds nothing here.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))

        notification_service.mark_all_read(session, user_id)
        session.commit()
        return JSONResponse(content={"message": "success"})

    @router.post("/notifications/{notification_id}", response_model=NotificationMarkRead)
    def mark_one_notification_read(
        request: Request,
        # Declared so the operation documents its path parameter — see
        # core.crud_router.path_parameter_block. `str`, never `int`.
        notification_id: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> NotificationMarkRead:
        """Toggle one notification's read state.

        Returns the notification with a ``read`` boolean. JWT-only: the route is mounted
        but not in the token registry, so an API token answers 403 where a JWT succeeds.
        Toggles ``read_at`` — null becomes now, a set value becomes null again.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        return notification_service.mark_one_read(
            session, path_param_as_id(notification_id), user_id
        )

    return router


#: The two token-grantable routes, for route_registry. ``GET`` is
#: ``notifications.read_all`` and the collection ``POST`` is
#: ``notifications.mark_all_as_read`` — the keys the reference server publishes. The
#: per-id ``POST /notifications/{id}`` is JWT-only and intentionally absent here:
#: registering it would let an API token reach it, which upstream forbids.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/notifications"),
    ("POST", "/api/v1/notifications"),
)
