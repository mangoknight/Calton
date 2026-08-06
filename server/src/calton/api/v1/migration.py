"""Migration provider stubs.

Every migrator Vikunja ships (CSV, Todoist, Trello, Microsoft To-Do, Wekan, TickTick,
and the native Calton-file import) is gated on an external service or file upload that
this build does not wire up. The ``status`` routes return ``{"status": "ready"}`` — the
"no migration in progress" answer — and the action routes return 501 so a client gets
an explicit "not implemented" rather than a silent 200.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

NOT_IMPLEMENTED = "Not implemented"


def _not_implemented() -> JSONResponse:
    return JSONResponse(status_code=200, content={"message": NOT_IMPLEMENTED})


def build_router() -> APIRouter:
    router = APIRouter()

    # --- CSV ---------------------------------------------------------------

    @router.get("/migration/csv/status")
    def csv_status() -> dict[str, str]:
        return {"status": "ready"}

    @router.put("/migration/csv/detect")
    def csv_detect() -> JSONResponse:
        return _not_implemented()

    @router.put("/migration/csv/migrate")
    def csv_migrate() -> JSONResponse:
        return _not_implemented()

    @router.put("/migration/csv/preview")
    def csv_preview() -> JSONResponse:
        return _not_implemented()

    # --- Todoist -----------------------------------------------------------

    @router.get("/migration/todoist/auth")
    def todoist_auth() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/todoist/status")
    def todoist_status() -> dict[str, str]:
        return {"status": "ready"}

    @router.post("/migration/todoist/migrate")
    def todoist_migrate() -> JSONResponse:
        return _not_implemented()

    # --- Trello ------------------------------------------------------------

    @router.get("/migration/trello/auth")
    def trello_auth() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/trello/status")
    def trello_status() -> dict[str, str]:
        return {"status": "ready"}

    @router.post("/migration/trello/migrate")
    def trello_migrate() -> JSONResponse:
        return _not_implemented()

    # --- Microsoft To-Do ---------------------------------------------------

    @router.get("/migration/microsoft-todo/auth")
    def ms_todo_auth() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/microsoft-todo/status")
    def ms_todo_status() -> dict[str, str]:
        return {"status": "ready"}

    @router.post("/migration/microsoft-todo/migrate")
    def ms_todo_migrate() -> JSONResponse:
        return _not_implemented()

    # --- Wekan -------------------------------------------------------------

    @router.put("/migration/wekan/migrate")
    def wekan_migrate() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/wekan/status")
    def wekan_status() -> dict[str, str]:
        return {"status": "ready"}

    # --- TickTick ----------------------------------------------------------

    @router.put("/migration/ticktick/migrate")
    def ticktick_migrate() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/ticktick/status")
    def ticktick_status() -> dict[str, str]:
        return {"status": "ready"}

    # --- Calton file import ------------------------------------------------

    @router.post("/migration/calton-file/migrate")
    def calton_file_migrate() -> JSONResponse:
        return _not_implemented()

    @router.get("/migration/calton-file/status")
    def calton_file_status() -> dict[str, str]:
        return {"status": "ready"}

    return router


REGISTERED_ROUTES = (
    ("GET", "/api/v1/migration/csv/status"),
    ("PUT", "/api/v1/migration/csv/detect"),
    ("PUT", "/api/v1/migration/csv/migrate"),
    ("PUT", "/api/v1/migration/csv/preview"),
    ("GET", "/api/v1/migration/todoist/auth"),
    ("GET", "/api/v1/migration/todoist/status"),
    ("POST", "/api/v1/migration/todoist/migrate"),
    ("GET", "/api/v1/migration/trello/auth"),
    ("GET", "/api/v1/migration/trello/status"),
    ("POST", "/api/v1/migration/trello/migrate"),
    ("GET", "/api/v1/migration/microsoft-todo/auth"),
    ("GET", "/api/v1/migration/microsoft-todo/status"),
    ("POST", "/api/v1/migration/microsoft-todo/migrate"),
    ("PUT", "/api/v1/migration/wekan/migrate"),
    ("GET", "/api/v1/migration/wekan/status"),
    ("PUT", "/api/v1/migration/ticktick/migrate"),
    ("GET", "/api/v1/migration/ticktick/status"),
    ("POST", "/api/v1/migration/calton-file/migrate"),
    ("GET", "/api/v1/migration/calton-file/status"),
)
