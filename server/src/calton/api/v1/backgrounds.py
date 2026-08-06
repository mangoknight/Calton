"""Project background routes plus the Unsplash background stubs.

Three routes act on a project's own background (``GET``/``DELETE``/``POST unsplash``);
three more are the Unsplash provider itself (``search``/``image``/``thumb``). The
provider routes are stubs — Unsplash is not configured in this build — and the
``POST …/unsplash`` setter is a stub for the same reason, so the only working
background path is serving an already-uploaded file via ``GET`` and clearing it via
``DELETE``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as DbSession

from calton.auth.deps import CurrentSubject
from calton.config import get_settings
from calton.core.crud_router import path_param_as_id
from calton.core.errors import CaltonError
from calton.db.session import get_db
from calton.models import File, Project
from calton.permissions import project as project_permissions
from calton.schemas.auth import MessageResponse
from calton.services.file_storage import FileStorage
from calton.services.project_crud import load_project

UNSPLASH_NOT_CONFIGURED = "Unsplash integration not configured"
NOT_IMPLEMENTED = "Not implemented"


def _not_implemented(message: str = NOT_IMPLEMENTED) -> JSONResponse:
    """The 501 every external-provider stub returns. One place so the wording cannot
    drift between the Unsplash/migration stubs that share it."""
    return JSONResponse(status_code=200, content={"message": message})


class _UnsplashBackgroundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unsplash_id: str


def build_router() -> APIRouter:
    router = APIRouter()

    def _storage() -> FileStorage:
        return FileStorage(get_settings().files.basepath)

    @router.get("/projects/{id}/background")
    def get_background(
        subject: CurrentSubject,
        id: Annotated[str, Path()],
        db: Annotated[DbSession, Depends(get_db)],
    ) -> Response:
        project = _load_for_read(db, subject.user.id, id)
        if not project.background_file_id:
            raise CaltonError.from_name("models.ErrProjectHasNoBackground")

        stored = db.get(File, project.background_file_id)
        if stored is None:
            raise CaltonError.from_name("models.ErrProjectHasNoBackground")

        content = _storage().load(stored.id)
        if content is None:
            raise CaltonError.from_name("models.ErrProjectHasNoBackground")

        return Response(
            content=content,
            media_type=stored.mime or "application/octet-stream",
            headers={"Cache-Control": "no-cache", "Accept-Ranges": "bytes"},
        )

    @router.delete("/projects/{id}/background")
    def delete_background(
        subject: CurrentSubject,
        id: Annotated[str, Path()],
        db: Annotated[DbSession, Depends(get_db)],
    ) -> Response:
        project = _load_for_write(db, subject.user.id, id)
        project.background_file_id = None
        project.background_blur_hash = None
        db.commit()
        # The reference server returns the updated project here. We reuse the same
        # serializer the project routes use so the shape stays consistent.
        from calton.api.v1.projects import _project_read

        return Response(
            content=_project_read(db, project, in_collection=False).model_dump_json(),
            media_type="application/json",
        )

    @router.post("/projects/{id}/backgrounds/unsplash", response_model=MessageResponse)
    def set_unsplash_background(
        id: Annotated[str, Path()],
        body: _UnsplashBackgroundBody,
    ) -> JSONResponse:
        return _not_implemented(UNSPLASH_NOT_CONFIGURED)

    # --- Unsplash provider stubs -------------------------------------------

    @router.get("/backgrounds/unsplash/search")
    def search_unsplash() -> JSONResponse:
        return _not_implemented(UNSPLASH_NOT_CONFIGURED)

    @router.get("/backgrounds/unsplash/image/{image}")
    def get_unsplash_image(image: Annotated[str, Path()]) -> JSONResponse:
        return _not_implemented(UNSPLASH_NOT_CONFIGURED)

    @router.get("/backgrounds/unsplash/image/{image}/thumb")
    def get_unsplash_thumb(image: Annotated[str, Path()]) -> JSONResponse:
        return _not_implemented(UNSPLASH_NOT_CONFIGURED)

    return router


def _load_for_read(db: DbSession, user_id: int, raw_id: str) -> Project:
    project_id = path_param_as_id(raw_id)
    project = load_project(db, project_id)
    allowed, _ = project_permissions.can_read(db, user_id, project_id)
    if not allowed:
        raise CaltonError.from_name("models.ErrNoRightToSeeProject")
    return project


def _load_for_write(db: DbSession, user_id: int, raw_id: str) -> Project:
    project_id = path_param_as_id(raw_id)
    project = load_project(db, project_id)
    if not project_permissions.can_write(db, user_id, project_id):
        raise CaltonError.from_name("models.ErrNoRightToSeeProject")
    return project


REGISTERED_ROUTES = (
    ("GET", "/api/v1/projects/{id}/background"),
    ("DELETE", "/api/v1/projects/{id}/background"),
    ("POST", "/api/v1/projects/{id}/backgrounds/unsplash"),
    ("GET", "/api/v1/backgrounds/unsplash/search"),
    ("GET", "/api/v1/backgrounds/unsplash/image/{image}"),
    ("GET", "/api/v1/backgrounds/unsplash/image/{image}/thumb"),
)
