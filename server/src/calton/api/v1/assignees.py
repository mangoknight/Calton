"""The four assignee endpoints (T26).

Thin on purpose: every rule lives in ``services.assignee_service``, which documents
where each one was measured. The only decisions taken here are the ones that belong to
the transport — how a path parameter becomes an id, and which response carries the
pagination headers.

⚠️ These routes look like the label routes and behave differently in five places. The
comparison table is in the service module; read it before changing anything here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import path_param_as_id
from calton.core.pagination import Paginator
from calton.db.session import get_db
from calton.schemas.assignee import (
    AssigneeCreated,
    AssigneeWrite,
    BulkAssignees,
    BulkAssigneesWrite,
)
from calton.schemas.message import Message
from calton.schemas.user import UserRead
from calton.services import assignee_service

#: Answered on every successful unassign, whether or not a row was removed.
DELETED_MESSAGE = "Successfully deleted."


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task}/assignees", response_model=list[UserRead])
    def read_assignees(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        task_id = path_param_as_id(task)
        assignees = assignee_service.list_assignees(
            session, task_id=task_id, user_id=auth_user_id(request)
        )

        page = paginator.slice(assignees)
        # Through Paginator.response so both pagination headers and the CORS exposure
        # travel with it — measured present here, including on the empty page, where the
        # count and the page total are both 0.
        return paginator.response(
            [entry.model_dump(mode="json") for entry in page],
            total_items=len(assignees),
            result_count=len(page),
        )

    @router.put("/tasks/{task}/assignees", status_code=201, response_model=AssigneeCreated)
    def add_assignee(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        body: AssigneeWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        task_id = path_param_as_id(task)
        return assignee_service.assign(
            session,
            task_id=task_id,
            assignee_id=body.user_id,
            user_id=auth_user_id(request),
            # Echoed straight back; never written. See AssigneeWrite.created.
            created=body.created,
        )

    @router.post("/tasks/{task}/assignees/bulk", status_code=201, response_model=BulkAssignees)
    def bulk_assign(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        body: BulkAssigneesWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        task_id = path_param_as_id(task)
        # The whole entries, not just their ids: the response echoes the user objects the
        # client sent, field for field. None and [] are carried through distinctly — the
        # echo differs between them.
        return assignee_service.bulk_assign(
            session,
            task_id=task_id,
            assignees=body.assignees,
            user_id=auth_user_id(request),
        )

    @router.delete("/tasks/{task}/assignees/{userID}", response_model=Message)
    def remove_assignee(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        userID: Annotated[str, Path(min_length=1)],  # noqa: N803 - the upstream path parameter's name
        session: Session = Depends(get_db),
    ) -> Any:
        task_id = path_param_as_id(task)
        assignee_id = path_param_as_id(userID)
        assignee_service.unassign(
            session, task_id=task_id, assignee_id=assignee_id, user_id=auth_user_id(request)
        )
        return Message(message=DELETED_MESSAGE)

    return router


#: Registered so an API token can reach these. The registry derives the group and action
#: from the path: (tasks_assignees, read_all/create/delete/update_bulk) — which is exactly
#: the set the parity corpus's `token_full` is granted. A route left out here is refused
#: for every token, JWT callers unaffected, so the omission only shows up under a token.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/tasks/{task}/assignees"),
    ("PUT", "/api/v1/tasks/{task}/assignees"),
    ("POST", "/api/v1/tasks/{task}/assignees/bulk"),
    ("DELETE", "/api/v1/tasks/{task}/assignees/{userID}"),
)
