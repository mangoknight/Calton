"""The two task-relation endpoints (T31).

Every rule lives in ``services.relation_service``; this module only turns path segments
into ids and decides which of the path and the body names the base task.

⚠️ ``PUT /tasks/{task}/relations`` takes the base task from the **body** when the body
supplies a ``task_id``. Echo binds path parameters before the body, so the body wins —
measured: pointed at ``/tasks/951/relations`` with ``{"task_id": 953}`` the relation lands
on task 953 and the response says 953. Permissions are resolved against the effective id,
so this is a wire quirk rather than a hole.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import deleted_response, path_param_as_id
from calton.db.session import get_db
from calton.schemas.message import Message
from calton.schemas.task_relation import TaskRelationCreated, TaskRelationWrite
from calton.services import relation_service


def build_router() -> APIRouter:
    router = APIRouter()

    @router.put("/tasks/{task}/relations", status_code=201, response_model=TaskRelationCreated)
    def create_relation(
        request: Request,
        task: str,
        body: TaskRelationWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        task_id = path_param_as_id(task) if body.task_id is None else body.task_id
        return relation_service.create_relation(
            session, task_id=task_id, data=body, user_id=auth_user_id(request)
        )

    @router.delete("/tasks/{task}/relations/{relationKind}/{otherTask}", response_model=Message)
    def delete_relation(
        request: Request,
        task: str,
        relationKind: str,  # noqa: N803 - upstream's path parameter name
        otherTask: str,  # noqa: N803 - upstream's path parameter name
        session: Session = Depends(get_db),
    ) -> Response:
        relation_service.delete_relation(
            session,
            task_id=path_param_as_id(task),
            # Passed through unvalidated on purpose: an unknown kind must reach the lookup
            # and take its 404, not a 400. See the service module.
            relation_kind=relationKind,
            other_task_id=path_param_as_id(otherTask),
            user_id=auth_user_id(request),
        )
        # `DeleteWeb` answers {"message": ...}, not the deleted resource.
        return deleted_response()

    return router


#: Registered so an API token can reach these — (tasks_relations, create/delete).
REGISTERED_ROUTES = (
    ("PUT", "/api/v1/tasks/{task}/relations"),
    ("DELETE", "/api/v1/tasks/{task}/relations/{relationKind}/{otherTask}"),
)
