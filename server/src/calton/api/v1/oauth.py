"""OpenID Connect callback stub.

The OAuth/OIDC flow needs an external provider configuration this build does not ship,
so the callback returns 501. The route is still registered (and auth-gated like the rest)
so the path exists in the OpenAPI surface and a client gets a structured 501 rather
than a 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import JSONResponse

NOT_IMPLEMENTED = "OpenID Connect is not configured"


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/auth/openid/{provider}/callback")
    def openid_callback(provider: Annotated[str, Path()]) -> JSONResponse:
        return JSONResponse(status_code=200, content={"message": NOT_IMPLEMENTED})

    return router


REGISTERED_ROUTES = (("POST", "/api/v1/auth/openid/{provider}/callback"),)
