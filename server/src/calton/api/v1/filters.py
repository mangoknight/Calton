"""Saved filter endpoints — **four** routes, hand-written rather than via CRUDRouter.

``CRUDRouter`` registers six: create, read_one, read_all, update (POST **and** PATCH) and
delete. Upstream's ``/filters`` has only four, and the two extras are not merely unused —
they are 405s on the reference server:

    GET   /api/v1/filters          -> 405   (there is no collection endpoint)
    PATCH /api/v1/filters/{filter} -> 405   (only POST updates)

Both measured, and both confirmed by ``GET /api/v1/routes``, whose ``filters`` group lists
exactly ``create``/``read_one``/``update``/``delete``. Mounting a CRUDRouter here would put
two operations into the OpenAPI document that upstream does not serve, which the AC-2
contract diff reports as additions — and would give a client two endpoints that work
against Calton and 405 against Calton, i.e. the one class of divergence this project
exists to prevent.

The item parameter is spelled ``{filter}``, matching ``param:"filter"`` on the Go struct
and ``/filters/:filter`` in ``routes.go``. Its name is not cosmetic: ``route_registry``
strips parameter segments to derive the permission group, so any spelling yields the group
``filters``, but the *swagger* path is compared verbatim by the contract diff.

⚠️ ``{filter}`` is declared **``str``**, never ``int``. A non-numeric id must answer
400/2004 (Echo fails at binding), and an ``int`` annotation makes FastAPI answer 422 with a
``detail`` body no v1 client can parse — as well as producing the ``InvalidSchema`` errors
schemathesis reports for handlers whose path parameters are undeclared. ``path_param_as_id``
does the conversion and raises the 400.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.core.crud_router import deleted_response, path_param_as_id, read_one_response
from calton.db.session import get_db
from calton.schemas.message import Message
from calton.schemas.saved_filter import (
    SavedFilterRead,
    SavedFilterWrite,
    SavedFilterWriteResponse,
)
from calton.services import saved_filter_service

#: Mounted and registered from the same constant, so the routing table and the API-token
#: permission table cannot disagree. A route missing from ``route_registry`` is not a
#: routing failure — it is a 403 on every API-token request while JWT requests keep
#: working, which has cost this project three separate investigations.
FILTER_COLLECTION_PATH = "/api/v1/filters"
FILTER_ITEM_PATH = "/api/v1/filters/{filter}"

REGISTERED_ROUTES = (
    ("PUT", FILTER_COLLECTION_PATH),
    ("GET", FILTER_ITEM_PATH),
    ("POST", FILTER_ITEM_PATH),
    ("DELETE", FILTER_ITEM_PATH),
)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.put("/filters", status_code=201, response_model=SavedFilterWriteResponse)
    def create_filter(
        request: Request,
        body: SavedFilterWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """201 with ``owner: null``.

        No permission check beyond being authenticated — ``CanCreate`` refuses only link
        shares, which Calton does not implement.
        """
        user_id = saved_filter_service.user_id_of(getattr(request.state, "auth", None))
        stored = saved_filter_service.create_filter(session, owner_id=user_id, body=body)
        payload = saved_filter_service.write_view(
            stored, created_is_real=True, echo=body
        ).model_dump(mode="json")
        # ``get_db`` closes the session without committing, so a service that only flushes
        # answers 201 with a fully populated body and persists nothing.
        session.commit()
        return JSONResponse(status_code=201, content=payload)

    @router.get("/filters/{filter}", response_model=SavedFilterRead)
    def read_filter(
        request: Request,
        # Named for the upstream path parameter (`/filters/:filter`); shadowing the
        # builtin is the lesser evil against a path the contract diff compares verbatim.
        filter: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """The only shape with a hydrated ``owner``.

        ⚠️ This route **does** send ``x-max-permission``, and always ``2``. It is
        registered as ``savedFiltersHandler.ReadOneWeb`` (``routes.go:811``) — the generic
        read handler — so it gets the header like every other ReadOne, and ``CanRead``
        answers ``PermissionAdmin`` unconditionally. Measured on the reference server; the
        write routes here send no such header. It is easy to assume the header belongs to
        projects because that is where it is interesting, and to leave it off a resource
        whose permission model has only one possible answer.
        """
        user_id = saved_filter_service.user_id_of(getattr(request.state, "auth", None))
        stored = saved_filter_service.load_for_read(session, user_id, path_param_as_id(filter))
        return read_one_response(
            saved_filter_service.read_view(session, stored).model_dump(mode="json"),
            saved_filter_service.SAVED_FILTER_MAX_PERMISSION,
        )

    @router.post("/filters/{filter}", response_model=SavedFilterWriteResponse)
    def update_filter(
        request: Request,
        # Named for the upstream path parameter (`/filters/:filter`); shadowing the
        # builtin is the lesser evil against a path the contract diff compares verbatim.
        filter: Annotated[str, Path(min_length=1)],
        body: SavedFilterWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Whole-model replacement, and ``filters`` is still required.

        Note the check order this produces: the body is validated by FastAPI *before* the
        handler runs, so ``POST /filters/9999 {"title": "x"}`` — a missing filter and an
        invalid body at once — answers **412**, not 404. That is also what upstream does
        (``ctx.Validate`` runs before ``CanUpdate``), and it is why the 404 case in the
        corpus sends a complete body.
        """
        user_id = saved_filter_service.user_id_of(getattr(request.state, "auth", None))
        # A body `id` shadows the path segment — see core.crud_router._effective_key.
        # Measured: `POST /filters/950 {"id": 1, ...}` as alice answers **403**, because
        # the ownership check follows the effective id and filter 1 belongs to someone
        # else. Reading the path instead answers 200 and edits alice's own filter.
        filter_id = body.id or path_param_as_id(filter)
        stored = saved_filter_service.load_for_write(session, user_id, filter_id)
        saved_filter_service.update_filter(session, stored, body)
        payload = saved_filter_service.write_view(
            stored, created_is_real=False, echo=body
        ).model_dump(mode="json")
        session.commit()
        return JSONResponse(content=payload)

    @router.delete("/filters/{filter}", response_model=Message)
    def delete_filter(
        request: Request,
        # Named for the upstream path parameter (`/filters/:filter`); shadowing the
        # builtin is the lesser evil against a path the contract diff compares verbatim.
        filter: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """``{"message": "Successfully deleted."}`` — not the deleted resource.

        The swagger annotation claims ``models.SavedFilter``; the route is registered as a
        ``DeleteWeb``, the shared handler, which answers ``models.Message`` for every
        resource that goes through it. Corrected in
        ``server/contract/swagger-corrections.yaml`` rather than implemented as annotated.
        """
        user_id = saved_filter_service.user_id_of(getattr(request.state, "auth", None))
        stored = saved_filter_service.load_for_write(session, user_id, path_param_as_id(filter))
        saved_filter_service.delete_filter(session, stored)
        session.commit()
        return deleted_response()

    return router
