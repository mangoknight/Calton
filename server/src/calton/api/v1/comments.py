"""The five task-comment endpoints (T30).

Thin on purpose: every rule lives in ``services.comment_service``, which records where
each one was measured. What is decided here belongs to the transport — how a path segment
becomes an id, which response carries the pagination headers, and the one place the
request body is allowed to override the URL.

⚠️ ``POST /tasks/{task}/comments/{commentid}`` takes the comment id from the **body** when
the body supplies one, falling back to the path. That looks wrong and is measured: Echo
binds path parameters before the body, so a body ``id`` wins. The permission check follows
the effective id, so it is not an escalation — but ignoring the body value would disagree
with upstream on both which comment is edited and what comes back.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import deleted_response, path_param_as_id, read_one_response
from calton.core.pagination import Paginator
from calton.db.session import get_db
from calton.schemas.message import Message
from calton.schemas.task_comment import (
    TaskCommentRead,
    TaskCommentWrite,
    TaskCommentWriteResponse,
)
from calton.services import comment_service


def _effective_comment_id(raw_path: object, body: TaskCommentWrite) -> int:
    """The body's ``id`` if it sent one, otherwise the path segment.

    ``0`` is not "sent nothing" — a body of ``{"id": 0}`` really does address comment 0
    upstream and answers 404/4015 — so the presence of the key is what decides, not its
    value.
    """
    if "id" in body.model_fields_set:
        return body.id
    return path_param_as_id(raw_path)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task}/comments", response_model=list[TaskCommentRead])
    def read_comments(
        request: Request,
        task: str,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        task_id = path_param_as_id(task)
        comments = comment_service.list_comments(
            session, task_id=task_id, user_id=auth_user_id(request)
        )

        page = paginator.slice(comments)
        return paginator.response(
            [entry.model_dump(mode="json") for entry in page],
            total_items=len(comments),
            result_count=len(page),
        )

    @router.get("/tasks/{task}/comments/{commentid}", response_model=TaskCommentRead)
    def read_comment(
        request: Request,
        task: str,
        commentid: str,
        session: Session = Depends(get_db),
    ) -> Response:
        task_id = path_param_as_id(task)
        comment, max_permission = comment_service.read_comment(
            session,
            task_id=task_id,
            comment_id=path_param_as_id(commentid),
            user_id=auth_user_id(request),
        )
        # ReadOne goes through the generic web handler, which attaches x-max-permission
        # (and its CORS exposure) to every single-resource read. Measured: '2' for the
        # project owner. The value is the permission on the comment's *task's project* —
        # comments hold none of their own — so it comes back from the service rather than
        # being recomputed here.
        return read_one_response(comment.model_dump(mode="json"), max_permission)

    @router.put("/tasks/{task}/comments", status_code=201, response_model=TaskCommentRead)
    def create_comment(
        request: Request,
        task: str,
        body: TaskCommentWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        return comment_service.create_comment(
            session,
            task_id=path_param_as_id(task),
            data=body,
            user_id=auth_user_id(request),
        )

    @router.post("/tasks/{task}/comments/{commentid}", response_model=TaskCommentWriteResponse)
    def update_comment(
        request: Request,
        task: str,
        commentid: str,
        body: TaskCommentWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        return comment_service.update_comment(
            session,
            task_id=path_param_as_id(task),
            comment_id=_effective_comment_id(commentid, body),
            data=body,
            user_id=auth_user_id(request),
        )

    @router.delete("/tasks/{task}/comments/{commentid}", response_model=Message)
    def delete_comment(
        request: Request,
        task: str,
        commentid: str,
        session: Session = Depends(get_db),
    ) -> Response:
        comment_service.delete_comment(
            session,
            task_id=path_param_as_id(task),
            comment_id=path_param_as_id(commentid),
            user_id=auth_user_id(request),
        )
        # `DeleteWeb`'s body is {"message": ...}, not the deleted resource — upstream's
        # swagger says otherwise and the swagger is wrong (registered as a
        # `response_fields` correction).
        return deleted_response()

    return router


#: Registered so an API token can reach these. The registry derives the group and action
#: from the path — (tasks_comments, read_all/read_one/create/update/delete). A route left
#: out here is refused for every token while JWT callers keep working, so the omission
#: only shows up under a token.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/tasks/{task}/comments"),
    ("GET", "/api/v1/tasks/{task}/comments/{commentid}"),
    ("PUT", "/api/v1/tasks/{task}/comments"),
    ("POST", "/api/v1/tasks/{task}/comments/{commentid}"),
    ("DELETE", "/api/v1/tasks/{task}/comments/{commentid}"),
)
