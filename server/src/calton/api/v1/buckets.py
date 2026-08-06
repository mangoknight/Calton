"""The five bucket / kanban endpoints (T28).

Thin on purpose: every measured rule lives in ``services.bucket_service``, which records
where each one came from. The only decisions here belong to the transport — turning path
parameters into ids, and which response carries the pagination headers.

⚠️ Two of these paths differ from each other by a single trailing segment and behave
differently in ways that look like bugs. Read the service module's header before changing
anything here.

Path parameters are declared ``str``, not ``int``. FastAPI answers 422 for a non-numeric
``int`` parameter, where upstream answers 400 / 2004 — so the conversion is done by
``path_param_as_id`` after routing rather than by the framework during it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import path_param_as_id
from calton.core.pagination import Paginator
from calton.db.session import get_db
from calton.schemas.bucket import BucketRead, BucketWrite
from calton.schemas.message import Message
from calton.schemas.task_bucket import TaskBucketRead
from calton.services import bucket_service

#: Answered on a successful bucket delete. ``DeleteWeb`` resources answer with a message
#: rather than the deleted object, whatever the swagger annotation claims.
DELETED_MESSAGE = "Successfully deleted."


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/projects/{project}/views/{view}/buckets",
        # BucketRead, not BucketSummary: upstream's schema declares `tasks` on a bucket,
        # so the contract diff requires it to be *declared* here — while
        # OmitEmptyCollection keeps it *absent from the body*, which is what this
        # endpoint was measured to do. Declaring the field and never emitting it is
        # the only way to satisfy both; dropping the field from the model instead
        # makes the contract diff report a missing response field forever.
        response_model=list[BucketRead],
    )
    def read_buckets(
        request: Request,
        project: str,
        view: str,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        buckets = bucket_service.list_buckets(
            session,
            project_id=path_param_as_id(project),
            view_id=path_param_as_id(view),
            user_id=auth_user_id(request),
        )
        page = paginator.slice(buckets)
        return paginator.response(
            [entry.model_dump(mode="json") for entry in page],
            total_items=len(buckets),
            result_count=len(page),
        )

    @router.put(
        "/projects/{project}/views/{view}/buckets",
        status_code=201,
        response_model=BucketRead,
    )
    def create_bucket(
        request: Request,
        project: str,
        view: str,
        body: BucketWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        return bucket_service.create_bucket(
            session,
            project_id=path_param_as_id(project),
            view_id=path_param_as_id(view),
            data=body,
            user_id=auth_user_id(request),
        )

    @router.post(
        "/projects/{project}/views/{view}/buckets/{bucket}",
        response_model=BucketRead,
    )
    def update_bucket(
        request: Request,
        project: str,
        view: str,
        bucket: str,
        body: BucketWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        return bucket_service.update_bucket(
            session,
            project_id=path_param_as_id(project),
            view_id=path_param_as_id(view),
            bucket_id=path_param_as_id(bucket),
            data=body,
            user_id=auth_user_id(request),
        )

    @router.delete(
        "/projects/{project}/views/{view}/buckets/{bucket}",
        response_model=Message,
    )
    def delete_bucket(
        request: Request,
        project: str,
        view: str,
        bucket: str,
        session: Session = Depends(get_db),
    ) -> Any:
        bucket_service.delete_bucket(
            session,
            project_id=path_param_as_id(project),
            view_id=path_param_as_id(view),
            bucket_id=path_param_as_id(bucket),
            user_id=auth_user_id(request),
        )
        return Message(message=DELETED_MESSAGE)

    @router.post(
        "/projects/{project}/views/{view}/buckets/{bucket}/tasks",
        response_model=TaskBucketRead,
    )
    def move_task_into_bucket(
        request: Request,
        project: str,
        view: str,
        bucket: str,
        body: dict[str, Any] | None = None,
        session: Session = Depends(get_db),
    ) -> Any:
        # An absent body and an absent `task_id` are the same case upstream: both
        # deserialise to task 0 and both answer 404/4002. Binding a model with a required
        # field would answer 412 for the empty body instead.
        task_id = 0
        if isinstance(body, dict):
            raw = body.get("task_id", 0)
            task_id = raw if isinstance(raw, int) else 0
        return bucket_service.move_task(
            session,
            project_id=path_param_as_id(project),
            view_id=path_param_as_id(view),
            bucket_id=path_param_as_id(bucket),
            task_id=task_id,
            user_id=auth_user_id(request),
        )

    return router


#: Registered so an API token can reach these. A route missing from the registry is
#: refused for every token while JWT callers keep working — an asymmetry that has bitten
#: this project three times, and which no unit test sees.
#:
#: ⚠️ The permission groups here are **not** guessable from the paths. The harness README
#: records the trap: ``GET /projects/:p/views/:v/tasks`` is its own group
#: (`projects_views_tasks.read_all`), while `projects.views_buckets_tasks` is the POST
#: that drops a task into a bucket. Getting one wrong takes down exactly one route and
#: reads like anything except a permissions problem. `GET /api/v1/routes` on a running
#: server is the authoritative table — never guess.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/projects/{project}/views/{view}/buckets"),
    ("PUT", "/api/v1/projects/{project}/views/{view}/buckets"),
    ("POST", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}"),
    ("DELETE", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}"),
    ("POST", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}/tasks"),
)
