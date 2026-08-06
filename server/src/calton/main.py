"""Application factory.

Importing this module must stay free of side effects — no settings are read, no database
is touched and no ``FastAPI`` instance is built until :func:`create_app` is called. Run it
with ``uvicorn calton.main:create_app --factory``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import Engine

from calton import __version__
from calton.api.v1 import admin as admin_api
from calton.api.v1 import (
    assignees,
    attachments,
    auth,
    buckets,
    comments,
    info,
    relations,
    testing,
    tokens,
    user,
)
from calton.api.v1 import backgrounds as backgrounds_api
from calton.api.v1 import caldav as caldav_api
from calton.api.v1 import filters as filters_api
from calton.api.v1 import labels as labels_api
from calton.api.v1 import migration as migration_api
from calton.api.v1 import notifications as notifications_api
from calton.api.v1 import oauth as oauth_api
from calton.api.v1 import projects as projects_api
from calton.api.v1 import routes as routes_endpoint
from calton.api.v1 import sharing as sharing_api
from calton.api.v1 import tasks as tasks_api
from calton.api.v1 import teams as teams_api
from calton.api.v1 import totp as totp_api
from calton.api.v1 import user_account as user_account_api
from calton.api.v1 import user_password as user_password_api
from calton.api.v1 import user_settings as user_settings_api
from calton.api.v1 import user_webhooks as user_webhooks_api
from calton.api.v1 import views as views_api
from calton.api.v1 import webhooks as webhooks_api
from calton.auth.deps import get_auth_subject
from calton.config import Settings, get_settings
from calton.core.errors import register_exception_handlers
from calton.core.route_registry import registry as route_registry
from calton.db.session import build_engine, session_factory

#: Where the Docker image drops the built ``web-react`` bundle.
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Everything below this prefix belongs to the API and is never served from disk.
API_PREFIX = "/api/"


def _resolve_static(root: Path, url_path: str) -> Path | None:
    """Map a URL path to a file inside ``root``, or None.

    Anything that escapes ``root`` resolves to None, so an encoded traversal cannot
    reach files outside the bundle.
    """
    candidate = (root / url_path.lstrip("/")).resolve()
    root = root.resolve()

    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _install_spa_fallback(app: FastAPI, static_dir: Path) -> None:
    """Serve the built frontend for anything the API did not handle.

    Deliberately a middleware rather than a mount or a catch-all route. Both of those
    are positional: a mount at ``/`` shadows every route registered after it, which is
    exactly the bug this replaces — ``GET /api/v1/info`` 404'd because ``include_router``
    ran after the mount, and it was invisible in CI because no bundle exists there. A
    middleware wraps the finished application, so it cannot shadow anything regardless of
    what is registered later or in what order.

    It is also not an exception handler, so it leaves the API's error responses (T04)
    entirely alone.
    """

    @app.middleware("http")
    async def serve_frontend(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        if response.status_code != 404 or request.method not in ("GET", "HEAD"):
            return response
        # An unknown API path stays a JSON 404. Never answer it with the app shell.
        if request.url.path.startswith(API_PREFIX):
            return response
        if not static_dir.is_dir():
            return response

        asset = _resolve_static(static_dir, request.url.path)
        if asset is not None:
            return FileResponse(asset)

        # A client-side route: hand back the shell and let the router sort it out.
        shell = static_dir / "index.html"
        if shell.is_file():
            return FileResponse(shell)

        return response


#: The auth line's non-CRUD routes, registered explicitly because they are not
#: CRUD resources: upstream files them under "other" and a token grants them one
#: subkey at a time — other.user reaches GET /user and nothing else.
AUTH_REGISTERED_ROUTES = [
    ("GET", "/api/v1/user"),
    ("GET", "/api/v1/users"),
    ("GET", "/api/v1/routes"),
]


def _install_api_cache_control(app: FastAPI) -> None:
    """Send ``Cache-Control: no-store`` on every routed /api/v1 response.

    Measured against the running Go server: it sets the header on /api/v1/info and
    on /api/v1/tasks (including that route's 401), but NOT on an unrouted path —
    so it is group middleware over the API, not a per-endpoint decoration.

    Worth its own middleware rather than a header on each handler: the parity
    harness compares this header byte-for-byte on every case, so getting it wrong
    once fails all 293 of them for a reason unrelated to what each case is testing.
    """

    @app.middleware("http")
    async def add_no_store(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # Measured against the running Go server, three cases:
        #   GET /api/v1/info        -> no-store   (routed, handler ran)
        #   GET /api/v1/tasks (401) -> no-store   (routed, handler ran)
        #   GET /api/v1/nonexistent -> absent     (never routed)
        #   HEAD /api/v1/info (405) -> absent     (routed by path, method rejected)
        # So the header tracks "a handler actually ran", not "a path matched".
        # Starlette still populates scope["route"] on a 405, hence the explicit
        # exclusion rather than relying on the route alone.
        routed = request.scope.get("route") is not None
        # Two escapes, both needed by the attachment download and measured on it:
        #
        #  * a handler that set Cache-Control itself keeps it — the download sends
        #    `no-cache`, not `no-store`, so that If-Modified-Since revalidation works;
        #  * a handler that asked for no header at all gets none — upstream's 416 carries
        #    no Cache-Control whatsoever, while the same endpoint's 403 and 404 do get
        #    `no-store` from here. So "absent" is a third state, not the same as unset.
        #
        # The opt-out travels in the request scope rather than in a marker header, so
        # there is nothing to strip off and nothing that can leak to the client.
        already_set = "Cache-Control" in response.headers
        opted_out = request.scope.get(attachments.SUPPRESS_CACHE_CONTROL, False)
        if (
            request.url.path.startswith("/api/")
            and routed
            and response.status_code != 405
            and not already_set
            and not opted_out
        ):
            response.headers["Cache-Control"] = "no-store"
        return response


def create_app(
    settings: Settings | None = None,
    static_dir: Path | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    static_dir = STATIC_DIR if static_dir is None else static_dir

    app = FastAPI(
        title="Calton",
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
        # Starlette redirects /path/ -> /path with a 307 by default; Echo just
        # 404s. Measured on the reference server: GET /api/v1/info/ is 404 there
        # and was 307 here. A redirect is not a harmless difference — a client
        # that does not follow redirects sees a completely different outcome, and
        # one that does silently converts a POST body into a second request.
        redirect_slashes=False,
    )
    app.state.settings = settings
    app.state.static_dir = static_dir

    # Must run before any router is added: without it FastAPI answers with its own
    # {"detail": ...} bodies and Starlette's plain-text 500, neither of which is a
    # v1 error shape. Until this call existed the whole of T04 was dead code.
    register_exception_handlers(app)

    # get_db reads the factory off app.state, so an app without it answers 500 on every
    # database-backed route. Tests that bring their own database overwrite this attribute
    # after create_app; building it here is what makes the default app usable at all.
    # The engine also has to be on app.state for the testing routes, which reach it
    # directly rather than through a module-level singleton.
    if engine is None:
        engine = build_engine(settings)
    app.state.engine = engine
    app.state.session_factory = session_factory(engine)

    app.include_router(info.build_router(settings), prefix="/api/v1")
    app.include_router(routes_endpoint.build_router(), prefix="/api/v1")

    # Gated exactly as upstream gates it (routes.go:523-527): present only when a
    # testing token is configured. This is what the parity harness resets through.
    if settings.service.testingtoken:
        app.include_router(testing.build_router(settings.service.testingtoken), prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.build_router(), prefix="/api/v1", tags=["auth"])
    app.include_router(user.build_router(), prefix="/api/v1", tags=["user"])
    app.include_router(tokens.build_router(), prefix="/api/v1", tags=["tokens"])
    # ⚠️ The `dependencies=` is what authenticates every task route.
    #
    # Unlike the three routers above, the task handlers do not take the subject as
    # an argument — they read `request.state.auth`. Nothing wrote it, so the whole
    # line answered 401 to a valid JWT (measured against the reference server: GET
    # /api/v1/user 200, GET /api/v1/tasks 401, same token). Attaching the resolver
    # at the include is what closes that gap for all of them at once.
    #
    # Every resource router mounted from here on needs this too. It is asserted
    # rather than remembered: TestTheAuthChainIsWired below walks the route
    # registry and fails on any registered route that does not answer 401
    # anonymously and something other than 401 with a real JWT.
    app.include_router(
        tasks_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        assignees.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        attachments.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )

    # Register what was mounted, so the API-token check can resolve these paths.
    # A route absent from the registry is refused outright, so mounting without
    # registering silently makes an endpoint JWT-only.
    for method, path in AUTH_REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in tasks_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in assignees.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in attachments.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Registering the router and registering the routes are two separate actions and
    # both are required: the API token check reads route_registry, so a route mounted
    # without its registry entry answers 403 to every token-authenticated request while
    # working fine with a JWT. That asymmetry has bitten this project three times.
    # The auth dependency was missing here when T16 merged: coder-a's branch predates
    # the auth wiring, so the text merged cleanly and the result was still wrong.
    # TestTheAuthChainIsWired caught it on the very next router — reporting
    # PUT /api/v1/projects -> 412, i.e. validation answering before authentication,
    # which is the shape this failure takes when it is not a flat 200.
    app.include_router(
        projects_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in projects_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Same two actions, and the same auth dependency. Mounting a router without it is
    # the failure described just above: the text merges cleanly and the result is a
    # route that validates the body before asking who is calling.
    app.include_router(
        views_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in views_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Labels come in two halves and BOTH have to be mounted. The CRUDRouter half is easy
    # to build and then forget to include — it is an object, not a decorator, so nothing
    # about constructing it makes it reachable, and every unit test against it still
    # passes. `test_labels_wiring.py` asserts all ten paths through `app.openapi()`.
    label_crud = labels_api.build_crud_router()
    app.include_router(
        label_crud.router, prefix="/api/v1", dependencies=[Depends(get_auth_subject)]
    )
    app.include_router(
        labels_api.build_router(), prefix="/api/v1", dependencies=[Depends(get_auth_subject)]
    )

    # Registering routes and registering their permission keys are two separate actions,
    # and skipping the second one does not break routing: it makes every API-token request
    # to these paths 403 while JWT requests keep working. That has already happened once in
    # this project. `register_crud_router` reads the CRUDRouter's own table so the mount
    # and the permission key can never come from different lists.
    route_registry.register_crud_router(label_crud)
    for method, path in labels_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Teams, same two halves for the same reason. The member routes are the hand-written
    # half: their `{username}` segment is a username rather than an id, so they cannot go
    # through CRUDRouter's item_param at all.
    team_crud = teams_api.build_crud_router()
    app.include_router(team_crud.router, prefix="/api/v1", dependencies=[Depends(get_auth_subject)])
    app.include_router(
        teams_api.build_router(), prefix="/api/v1", dependencies=[Depends(get_auth_subject)]
    )
    # Notifications: the read plus the collection-level mark-all. The per-id toggle is
    # not implemented — no API token can reach it upstream.
    app.include_router(
        notifications_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in notifications_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # The three sharing creates. Their read/delete siblings are out of this phase's scope.
    app.include_router(
        sharing_api.build_router(), prefix="/api/v1", dependencies=[Depends(get_auth_subject)]
    )
    for method, path in sharing_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    route_registry.register_crud_router(team_crud)
    for method, path in teams_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # ⚠️ Webhooks are mounted **conditionally**, which nothing else here is. Upstream does
    # not register these four routes when `webhooks.enabled` is false, and the parity
    # harness currently runs the Go side with it false while the MCP gate runs it true —
    # so a hard mount would be a divergence on one of the two planes no matter which way
    # it was written. The flag defaults to true, as upstream. See config.WebhooksSettings.
    #
    # The registry entries are registered under the same condition: a permission key for
    # a route that is not mounted would offer a grant that authorises nothing, which is
    # exactly what route_registry exists to prevent.
    if settings.webhooks.enabled:
        app.include_router(
            webhooks_api.build_router(),
            prefix="/api/v1",
            dependencies=[Depends(get_auth_subject)],
        )
        for method, path in webhooks_api.REGISTERED_ROUTES:
            route_registry.register(method, path)

    # Comments (T30) and relations (T31). Both hang off /tasks/{task} but are separate
    # permission groups — tasks_comments and tasks_relations — so a token granted
    # tasks.update reaches neither. The `dependencies=` is what authenticates them; without
    # it these handlers read an unset request.state.auth and every call is a 401.
    app.include_router(
        comments.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        relations.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in comments.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in relations.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Buckets (T28). Mounted with the auth dependency like every resource router above,
    # and registered separately — the two are different actions and skipping the second
    # leaves these routes working for JWTs and 403 for every API token.
    app.include_router(
        buckets.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in buckets.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Saved filters are hand-written rather than a CRUDRouter: upstream serves four routes
    # here, not six (no `GET /filters`, no PATCH — both are 405 on the reference server).
    # See api/v1/filters. The mount and the registry read the same REGISTERED_ROUTES, so
    # they cannot drift; without the registry half every API-token request to /filters
    # would be 403 while JWT requests kept working.
    app.include_router(
        filters_api.build_router(), prefix="/api/v1", dependencies=[Depends(get_auth_subject)]
    )
    for method, path in filters_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # --- Account-management, background, migration and OAuth routers ----------------
    #
    # These five are the remaining ``other.user`` and external-integration routes. They
    # are mounted WITHOUT an include-level ``dependencies=[Depends(get_auth_subject)]``
    # on purpose: several of their handlers are callable anonymously — the password
    # reset / email-confirm / reset-token flows act on a token rather than the caller,
    # and the migration/Unsplash/OAuth stubs must return their 501 regardless of
    # credentials. The handlers that DO need the caller take ``CurrentSubject`` (which
    # itself depends on ``get_auth_subject``), so auth is enforced exactly where it
    # matters and anonymous token flows are not gated shut. This mirrors the existing
    # ``user`` router, which is mounted the same way for the same reason.
    app.include_router(
        user_password_api.build_router(),
        prefix="/api/v1",
        tags=["user"],
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        user_account_api.build_router(),
        prefix="/api/v1",
        tags=["user"],
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        backgrounds_api.build_router(),
        prefix="/api/v1",
        tags=["background"],
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        migration_api.build_router(),
        prefix="/api/v1",
        tags=["migration"],
        dependencies=[Depends(get_auth_subject)],
    )
    app.include_router(
        oauth_api.build_router(),
        prefix="/api/v1",
        tags=["auth"],
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in user_password_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in user_account_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in backgrounds_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in migration_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    for method, path in oauth_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # --- User settings, TOTP, CalDAV tokens, user webhooks, admin --------------
    #
    # The authenticated user-settings / TOTP / CalDAV / user-webhook routers mount
    # with the auth dependency, exactly as every resource router above does: their
    # handlers read the caller off ``request.state.auth`` via ``auth_user_id``.
    #
    # The admin and CalDAV prefixes are JWT-only (``auth.deps.JWT_ONLY_PREFIXES``),
    # so an API token is refused there regardless of grant — hence their
    # ``REGISTERED_ROUTES`` are empty and no permission group is offered for them.
    #
    # The public avatar route (``GET /{username}/avatar``) is mounted WITHOUT the
    # auth dependency: avatars are served to anonymous viewers on shared boards, so
    # gating them would break the frontend. It carries no ``request.state.auth``.
    app.include_router(
        user_settings_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in user_settings_api.REGISTERED_ROUTES:
        route_registry.register(method, path)
    # Public avatar serving — no auth dependency, deliberately unregistered.
    app.include_router(user_settings_api.build_avatar_router(), prefix="/api/v1")

    app.include_router(
        totp_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in totp_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    app.include_router(
        caldav_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in caldav_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    app.include_router(
        user_webhooks_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in user_webhooks_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    app.include_router(
        admin_api.build_router(),
        prefix="/api/v1",
        dependencies=[Depends(get_auth_subject)],
    )
    for method, path in admin_api.REGISTERED_ROUTES:
        route_registry.register(method, path)

    # Both middlewares wrap the finished app, so they are installed after every
    # router. Order matters only between the two: the cache-control header must be
    # set on the API's own responses, and the SPA fallback must not see them —
    # it only ever rewrites a 404 that the API declined.
    _install_api_cache_control(app)
    _install_spa_fallback(app, static_dir)

    return app
