"""CRUDRouter — the Python counterpart of Go's handler.WebHandler.

Roughly 59 of Phase 1's endpoints are registered through here rather than written
by hand, so every quirk fixed in this file is fixed 59 times.

⚠️ v1 INVERTS THE HTTP VERBS. PUT creates, POST updates. This is the opposite of
REST convention and of /api/v2, and it is not negotiable — it is what every
existing client sends (pkg/models/api_routes.go:137-150):

    PUT    /labels          -> create
    POST   /labels/{label}  -> update
    DELETE /labels/{label}  -> delete
    GET    /labels/{label}  -> read_one
    GET    /labels          -> read_all

⛔ **No PATCH.** This table used to list `PATCH /labels/{label} -> update`, and it was
wrong: upstream answers **405** to PATCH on every CRUD item path — measured on labels,
tasks, projects and filters, each with a POST control proving the route exists. Nothing
asserted the line, so it sat here directing the next reader to register a verb upstream
does not serve. `test_patch_is_not_served_on_the_item_paths` now holds it.

Getting this backwards produces a 404, not an error message, and nothing in any
log says the verb was wrong. The mapping lives here and only here.

⚠️ UPDATE REPLACES THE WHOLE MODEL. A field the body omits is reset to its zero
value, not left alone. MCP clients rely on this: eargollo does read-modify-write
against projects, filters, tasks and labels precisely because it knows the
endpoint is a full replace. Implementing a partial update does not fail any
request — it silently does the wrong thing to users' data.

**Exception: some pointer fields opt out of the reset.** Whether a field is
exempt is decided by one thing only — read that model's ``Update()`` and look at
how it builds ``Cols`` and whether it guards on nil. "Is it a persisted pointer?"
is *not* the test: ``ProjectView.Filter`` is a persisted pointer that enters Cols
unconditionally, so omitting it clears it. Known exemptions live in the design
doc §2.3.1; in Phase 1 they are ``Project.ParentProjectID`` (T16) and
``SavedFilter.Filters`` (T29). Each needs the four-cell matrix — omitted / null /
explicit zero / explicit value — because omitted and explicit-zero must stay
distinguishable, and that is exactly what a non-Optional default collapses.

The same read-modify-write habit means write schemas must set
``extra="ignore"``: clients send back the entire object they read, including
``owner``, ``max_right``, ``identifier``, ``created`` and sometimes whole nested
collections. Extra fields must never yield a 422.

⚠️ THE THREE WRITE ROUTES COMMIT; THE SERVICES ONLY FLUSH. ``get_db`` yields a
session and closes it without committing, so a service that merely flushes has
its work rolled back the moment the request ends — and every symptom points
somewhere else, because the response is built before the rollback and is
completely correct. The request answers 201 with the created object and the row
is not there afterwards. Committing here rather than in each service puts it at
the request boundary, where it cannot be forgotten by the next resource; labels
were the first resource mounted through this router and lost every write.
"""

# NOTE: no `from __future__ import annotations` here. FastAPI resolves route
# annotations at registration time, and stringified annotations referring to a
# closure variable (the write schema) cannot be resolved from module globals.
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from calton.core.errors import INVALID_MODEL_MESSAGE, CaltonError
from calton.core.pagination import PAGINATION_EXPOSE_HEADERS, Paginator, paginated_response
from calton.core.policy import (
    FORBIDDEN_READ_MESSAGE,
    ForbiddenError,
    Policy,
)
from calton.db.session import get_db
from calton.schemas.message import Message

ModelT = TypeVar("ModelT")
ReadT = TypeVar("ReadT", bound=BaseModel)
WriteT = TypeVar("WriteT", bound=BaseModel)

MAX_PERMISSION_HEADER = "x-max-permission"
MAX_PERMISSION_EXPOSE_HEADERS = "x-max-permission"
DELETE_MESSAGE = "Successfully deleted."

#: Echo's binding failure message, measured from the reference server. Aliased rather than
#: re-spelled: it is the same string on the body path, and keeping two copies is how the
#: two came to disagree in the first place (see ``errors.INVALID_MODEL_MESSAGE``).
_INVALID_MODEL_MESSAGE = INVALID_MODEL_MESSAGE


class Action(StrEnum):
    """Route actions, named as pkg/models/api_routes.go names them.

    These strings are also the second half of the API token permission keys, so
    they are a wire contract, not an internal enum.
    """

    CREATE = "create"
    READ_ONE = "read_one"
    READ_ALL = "read_all"
    UPDATE = "update"
    DELETE = "delete"


def action_for(method: str, has_path_param: bool, v2: bool = False) -> Action:
    """Map an HTTP verb onto an action, copying api_routes.go:137-150.

    ``has_path_param`` distinguishes a collection route from an item route, which
    is the only thing separating read_all from read_one.
    """
    method = method.upper()
    if method == "PUT":
        return Action.UPDATE if v2 else Action.CREATE
    if method == "POST":
        return Action.CREATE if v2 else Action.UPDATE
    if method == "PATCH":
        return Action.UPDATE
    if method == "DELETE":
        return Action.DELETE
    if method == "GET":
        return Action.READ_ONE if has_path_param else Action.READ_ALL
    raise ValueError(f"no action for HTTP method {method}")


class CrudService(Protocol[ModelT]):
    """What a resource's service layer must provide.

    ``read_all`` returns both counts because the pagination headers need them:
    ``result_count`` is this page, ``total_items`` is everything matching.

    Every method takes the request's ``session``, the same one handed to the Policy.
    See :class:`calton.core.policy.Policy` for why they must not open their own.
    """

    def create(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> ModelT: ...

    def read_one(self, session: Session, auth: Any, **kwargs: Any) -> ModelT: ...

    def read_all(
        self, session: Session, auth: Any, search: str, page: int, per_page: int, **kwargs: Any
    ) -> tuple[list[ModelT], int, int]: ...

    def update(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> ModelT: ...

    def delete(self, session: Session, auth: Any, **kwargs: Any) -> None: ...


#: Exemptions granted so far, for audit. Empty is the expected state:
#: encoding/json is strict across the board, so there should be no legitimate
#: reason to relax. An entry here means the parity harness found somewhere Go is
#: genuinely lax, and the justification names the source that proves it.
STRICT_EXEMPTIONS: dict[str, str] = {}

#: A justification has to actually cite something. These are the markers of a
#: real source reference; "legacy client" or "it broke the tests" are not.
_EVIDENCE_MARKERS = ("pkg/", ".go:", "http")


def _require_strict(write_schema: type[BaseModel], strict_exempt: str | None) -> None:
    """Refuse a write schema that would coerce types Go's decoder rejects.

    Pydantic's default lax mode turns ``{"done": "yes"}`` into ``True`` and
    ``{"priority": "3"}`` into ``3``. encoding/json refuses both, so upstream
    answers 400 and stores nothing — while we would answer 200 and persist a
    value the user never sent. That is a data-correctness problem, not a
    response-shape one, and it is worst with LLM clients, which emit loosely
    typed JSON constantly.

    Enforced at construction rather than documented, because this is the base for
    roughly 59 endpoints and a missing config flag stays invisible until data is
    already wrong.

    ``strict_exempt`` is the escape hatch, and it is deliberately awkward: it must
    be a justification citing a Go source location. The point is that if the
    parity harness ever shows Go being genuinely lax somewhere, the answer is a
    documented exemption for that one resource — not deleting the check.
    """
    if strict_exempt is not None:
        justification = strict_exempt.strip()
        if not justification or not any(marker in justification for marker in _EVIDENCE_MARKERS):
            raise ValueError(
                f"strict_exempt for {write_schema.__name__} must cite Go source "
                f"(e.g. 'pkg/models/foo.go:123 accepts a string here'); got {strict_exempt!r}"
            )
        STRICT_EXEMPTIONS[write_schema.__name__] = justification
        return

    if not write_schema.model_config.get("strict"):
        raise ValueError(
            f"{write_schema.__name__} must set model_config = ConfigDict(strict=True): "
            "lax coercion accepts writes that upstream rejects outright. If Go is "
            "genuinely lax here, pass strict_exempt='<reason citing pkg/...>'"
        )


def read_one_response(payload: Any, max_permission: int) -> JSONResponse:
    """A ReadOne body plus its permission header (read_one.go:58-60).

    The Expose-Headers value here is ``x-max-permission`` alone — upstream sets it
    separately from the pagination one, and browsers only ever see one of the two
    on a given response.
    """
    return JSONResponse(
        content=payload,
        headers={
            MAX_PERMISSION_HEADER: str(max_permission),
            "Access-Control-Expose-Headers": MAX_PERMISSION_EXPOSE_HEADERS,
        },
    )


def deleted_response() -> JSONResponse:
    """delete.go:79 — a message object, not an empty 204."""
    return JSONResponse(content={"message": DELETE_MESSAGE})


class CRUDRouter[ModelT, ReadT: BaseModel, WriteT: BaseModel]:
    """Registers the five standard operations for one resource.

    Every operation follows the same pipeline as the Go handlers: bind, check the
    policy, call the service, then serialise with the right status and headers.
    """

    def __init__(
        self,
        *,
        prefix: str,
        item_param: str,
        service: CrudService[ModelT],
        policy: Policy,
        read_schema: type[ReadT],
        write_schema: type[WriteT],
        serialize: Any = None,
        serialize_write: Any = None,
        strict_exempt: str | None = None,
        read_all_params: Any = None,
    ) -> None:
        self.prefix = prefix.rstrip("/")
        self.item_param = item_param
        self.service = service
        self.policy = policy
        self.read_schema = read_schema
        _require_strict(write_schema, strict_exempt)
        self.write_schema = write_schema
        self._serialize = serialize or self._default_serialize
        # Separate hook for the two write routes. Defaults to the read one, which is
        # correct for both resources mounted here today and is *not* a general rule —
        # see `_render_write`.
        self._serialize_write = serialize_write or self._serialize
        # Opt-in: a resource whose collection route takes query parameters beyond
        # s/page/per_page supplies a callable turning the request into extra kwargs for
        # its read_all. Only /projects needs it so far (?is_archived=true). Left as None
        # for every other resource, so their signatures are unchanged.
        self._read_all_params = read_all_params
        self.router = APIRouter()
        self._register()

    def _default_serialize(self, model: ModelT, session: Session, in_collection: bool) -> Any:
        return self.read_schema.model_validate(model, from_attributes=True).model_dump(mode="json")

    def _render_read(self, model: ModelT, session: Session, *, in_collection: bool = False) -> Any:
        """Serialise one model for ``GET`` — the item route and the collection route.

        ``session`` and ``in_collection`` are passed to every serializer because some
        resources genuinely need them: a project's response embeds its owner and views
        (a query) and answers *differently* on the collection than on the item. A
        serializer that cannot see which of the two it is producing has to pick one shape
        for both, and for projects either choice is wrong half the time.
        """
        return self._serialize(model, session, in_collection)

    def _render_write(self, model: ModelT, session: Session) -> Any:
        """Serialise one model for ``PUT``/``POST``. Never called with a collection.

        **Separate from the read path even though both resources here pass the same
        function**, and the reason is worth stating because "they are identical, fold
        them together" is the obvious review comment.

        Upstream's write response is the *bound request struct* — the object the handler
        validated, not a re-read of the row — so for most resources it echoes back
        whatever the client sent, zero values included. The exception is a model whose
        ``Update`` **overwrites its receiver wholesale** from storage before returning.
        Exactly two of the seven updatable resources do that, and they are the two mounted
        on this router:

        ============================  ==========================================
        resource                      why its write response holds real values
        ============================  ==========================================
        ``POST /labels/{label}``      ``Label.Update`` ends ``l.ReadOne(s, a)``
        ``POST /projects/{project}``  ``UpdateProject`` ends ``*project = *l`` then
                                      ``project.ReadOne(s, auth)``
        ============================  ==========================================

        The other five (task, saved filter, project view, bucket, task comment) echo, and
        each renders through its own service. ⚠️ "It re-reads the row" is **not** the
        criterion: ``SavedFilter.Update`` re-reads too, but copies only ``OwnerID`` back
        onto the receiver, and it echoes. Measured — posting a forged ``created`` to a
        saved filter answers with the forgery, and to a project or label answers with the
        stored value.

        So the two hooks being equal is a fact about these two resources, not a property
        of the router. A resource whose ``Update`` does not end that way must pass its own
        ``serialize_write``; folding the hooks back into one would make that impossible to
        express and the mistake invisible, since the read shape is the plausible-looking
        answer in every case.
        """
        return self._serialize_write(model, session, False)

    @property
    def item_path(self) -> str:
        return f"{self.prefix}/{{{self.item_param}}}"

    def _register(self) -> None:
        router = self.router
        item_path = self.item_path
        # Bound locally so the annotations below are real classes, not attribute
        # lookups FastAPI would have to resolve on its own.
        write_schema = self.write_schema
        read_schema = self.read_schema
        # Declared for the OpenAPI document only; the handlers still read the id off the
        # request. See path_parameter_block for why both halves are necessary.
        item_param_doc = {"parameters": [path_parameter_block(self.item_param)]}

        # ⚠️ Every route below declares ``response_model=`` even though its handler
        # returns a ``Response`` and FastAPI therefore skips response validation at
        # runtime. The declaration is not for runtime — it is the *contract*.
        #
        # A handler annotated only ``-> Response`` documents nothing at all, exactly as a
        # ``-> dict[str, Any]`` one does (convention C-1). The consequence is silent and
        # bad in both directions: the AC-2 contract diff compares response field names,
        # and comparing against an empty set passes **vacuously**, so all 59 endpoints
        # registered here would certify as matching upstream while declaring no fields;
        # and openapi-typescript hands the frontend ``{}``. Measured when projects were
        # first wired: the diff reported all 17 project fields missing on four
        # operations at once.
        #
        # Passing a Response through keeps the wire bytes exactly as the serializer
        # built them — pagination headers, x-max-permission and the omitted-key rules
        # all survive — while the schema still describes what was sent.
        #
        # Both lines hit this independently in the same window: labels was the first
        # resource mounted here and its diff reported all seven fields missing on all
        # five operations.
        @router.put(self.prefix, status_code=201, response_model=read_schema)
        def create(
            request: Request,
            body: write_schema,  # type: ignore[valid-type]
            session: Session = Depends(get_db),
        ) -> Response:
            if not self.policy.can_create(session, auth=_auth_of(request)):
                raise ForbiddenError()
            created = self.service.create(session, body, auth=_auth_of(request))
            payload = self._render_write(created, session)
            session.commit()
            return JSONResponse(status_code=201, content=payload)

        # POST on the item updates, replacing the model wholesale.
        @router.post(item_path, response_model=read_schema, openapi_extra=item_param_doc)
        def update(
            request: Request,
            body: write_schema,  # type: ignore[valid-type]
            session: Session = Depends(get_db),
        ) -> Response:
            key = _effective_key(request, body, self.item_param)
            if not self.policy.can_update(session, auth=_auth_of(request), **key):
                raise ForbiddenError()
            updated = self.service.update(session, body, auth=_auth_of(request), **key)
            payload = self._render_write(updated, session)
            session.commit()
            return JSONResponse(content=payload)

        # ⛔ **No PATCH.** Upstream registers PATCH on exactly four paths — `/test/:table`
        # and three `/admin` routes — and none of them is in Phase 1. Measured on the
        # reference server: `PATCH /tasks/950`, `/projects/950` and `/labels/950` all
        # answer **405** with `{"message":"Method Not Allowed"}` and
        # `Allow: OPTIONS, DELETE, GET, POST`.
        #
        # Registering it here looked free — it is the same handler as POST, and "PATCH
        # updates" is what every other REST API does — but a verb upstream does not serve
        # is an **extra operation at the contract layer**, not a convenience: it is absent
        # from the Phase 1 whitelist, so nothing was comparing it, and a client that
        # feature-detects by trying PATCH would branch differently against the two servers.
        # Deleting it is what makes Starlette produce the 405 above, since the path still
        # exists for other methods.

        @router.get(item_path, response_model=read_schema, openapi_extra=item_param_doc)
        def read_one(request: Request, session: Session = Depends(get_db)) -> Response:
            key = _path_key(request, self.item_param)
            allowed, max_permission = self.policy.can_read(session, auth=_auth_of(request), **key)
            if not allowed:
                raise ForbiddenError(FORBIDDEN_READ_MESSAGE)
            model = self.service.read_one(session, auth=_auth_of(request), **key)
            return read_one_response(self._render_read(model, session), max_permission)

        # ``list[read_schema]`` built at runtime: read_schema is a variable, so mypy
        # cannot read it as a type annotation even though FastAPI resolves it fine.
        collection_schema = list[read_schema]  # type: ignore[valid-type]

        @router.get(self.prefix, response_model=collection_schema)
        def read_all(request: Request, session: Session = Depends(get_db)) -> Response:
            # No permission gate here, deliberately. DoReadAll (handler/core.go:111-130)
            # runs no check at all: each resource's ReadAll scopes its own query by
            # the caller, so "you may list this collection" is not a question the
            # upstream API asks. An earlier version called a keyless can_read() —
            # a concept that does not exist upstream — which turned the ordinary
            # "user has no labels yet" case into a 403 where Calton returns
            # 200 []. Visibility belongs to the service layer; enforce it there.
            paginator = Paginator(
                page=request.query_params.get("page", "1"),
                per_page=request.query_params.get("per_page", ""),
            )
            extra = self._read_all_params(request) if self._read_all_params else {}
            items, result_count, total_items = self.service.read_all(
                session,
                auth=_auth_of(request),
                search=request.query_params.get("s", ""),
                page=paginator.page,
                per_page=paginator.per_page,
                **extra,
            )
            return paginated_response(
                [self._render_read(item, session, in_collection=True) for item in items],
                total_items=total_items,
                per_page=paginator.per_page,
                result_count=result_count,
            )

        @router.delete(item_path, response_model=Message, openapi_extra=item_param_doc)
        def delete(request: Request, session: Session = Depends(get_db)) -> Response:
            key = _path_key(request, self.item_param)
            if not self.policy.can_delete(session, auth=_auth_of(request), **key):
                raise ForbiddenError()
            self.service.delete(session, auth=_auth_of(request), **key)
            session.commit()
            return deleted_response()

    def registered_actions(self) -> list[tuple[str, str, Action]]:
        """(method, path, action) for everything this router registered.

        T08's route_registry consumes this, and the API token check reads the
        same table, so the two can never disagree.
        """
        return [
            ("PUT", self.prefix, Action.CREATE),
            ("POST", self.item_path, Action.UPDATE),
            # No PATCH entry: the router does not register one — see the comment on the
            # missing route above. Leaving it here would grant tokens a permission for a
            # route that answers 405.
            ("GET", self.item_path, Action.READ_ONE),
            ("GET", self.prefix, Action.READ_ALL),
            ("DELETE", self.item_path, Action.DELETE),
        ]


def _auth_of(request: Request) -> Any:
    """The authenticated subject. Populated by T14/T15; None until then."""
    return getattr(request.state, "auth", None)


#: int64, because that is what the Go column holds. A larger value is a 400 upstream,
#: and Python's unbounded ints would otherwise accept it and fail later or silently.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def path_param_as_id(raw: object) -> int:
    """Convert a path parameter to an id, the way upstream does.

    Echo binds path parameters onto an int64 struct field, so a non-numeric or
    out-of-range value fails at binding and never reaches a handler: the answer is
    400 ``{"code":2004}``, not FastAPI's default 422. Measured against the reference
    server, which also accepts **negative** ids — ``-1`` is the Favorites pseudo project
    — so this must not reject them.
    """
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise CaltonError.from_name(
            "models.ErrInvalidModel", message=_INVALID_MODEL_MESSAGE
        ) from None

    if not _INT64_MIN <= value <= _INT64_MAX:
        raise CaltonError.from_name("models.ErrInvalidModel", message=_INVALID_MODEL_MESSAGE)

    return value


def path_parameter_block(name: str) -> dict[str, Any]:
    """An OpenAPI ``parameters`` entry for one path parameter, typed as a string.

    Handlers here read their id from ``request.path_params`` rather than from a typed
    argument, because an ``int`` annotation makes FastAPI answer **422** where upstream
    answers **400/2004** — Echo fails at binding, and 422 with a ``detail`` body is a
    status this API never emits. The cost of reading it off the request is that FastAPI
    generates **no ``parameters`` block at all**, and an operation whose path has ``{x}``
    but declares no ``x`` is one schemathesis refuses to fuzz: ``InvalidSchema: Path
    parameter 'label' is not defined``. 22 operations were dark that way — the gate looked
    green because a skipped case and a passing case both count as not-failing.

    So the parameter is declared *for the document* while the handler keeps reading it from
    the request. ``type: string`` is deliberate and matches what a handler that did declare
    it would say: it is what lets the fuzzer send ``abc`` and confirm the 400, which an
    ``integer`` schema would never generate.

    ``in: path`` parameters are skipped on both sides of the contract diff
    (``contract/golden._required_params``), so adding them here cannot make the diff report
    a requiredness difference against upstream's swaggo-generated spec.
    """
    # ``minLength: 1`` is not decoration. An unconstrained ``type: string`` makes the
    # fuzzer generate values that cannot be a path segment at all — the empty string above
    # all — which it then discards, and enough discards trip hypothesis's ``filter_too_much``
    # health check and fail the operation for a reason unrelated to the endpoint. It costs
    # nothing in behaviour: a request with an empty segment does not route here in the first
    # place, so no value this rejects could ever have reached the handler.
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string", "minLength": 1},
    }


def _path_key(request: Request, param: str) -> dict[str, Any]:
    """The item key, converted once here rather than in every service."""
    return {param: path_param_as_id(request.path_params.get(param))}


def _effective_key(request: Request, body: Any, param: str) -> dict[str, Any]:
    """The item key an **update** acts on: the body's ``id`` when it has one, else the path.

    ⚠️ This looks like a security hole and is upstream's behaviour. Echo binds path
    parameters before the body (``ctx.Bind`` runs second), so a body field named ``id``
    overwrites the value taken from the URL, and the handler then acts on whichever object
    the body named. Measured on the reference service:

        POST /api/v1/labels/950  {"title": "X", "id": 951}
        -> 200 {"id": 951, ...}; label 951 is renamed and 950 is untouched

    The same holds for tasks and projects. It is not a permission bypass — the policy
    below runs against the *effective* id, so naming somebody else's object answers 403
    (measured: ``POST /filters/950 {"id": 1}`` is 403, because filter 1 is not the
    caller's). What it does change is **which row a write lands on**, and both readings
    answer 200, so nothing but a read-back can tell them apart.

    Honouring the path instead is the safer-looking choice and silently edits a different
    row than upstream would. If we ever want that, it is a deliberate deviation with a
    register entry, not a quiet correction here.
    """
    body_id = getattr(body, "id", 0)
    if isinstance(body_id, int) and not isinstance(body_id, bool) and body_id != 0:
        return {param: body_id}
    return _path_key(request, param)


__all__ = [
    "MAX_PERMISSION_EXPOSE_HEADERS",
    "MAX_PERMISSION_HEADER",
    "PAGINATION_EXPOSE_HEADERS",
    "Action",
    "CRUDRouter",
    "CrudService",
    "action_for",
    "deleted_response",
    "path_param_as_id",
    "read_one_response",
]
