"""Error responses, byte-compatible with the Go implementation.

The Go side funnels everything through one Echo handler
(pkg/routes/error_handler.go). Three body shapes come out of it and we reproduce
all three, including the inconsistencies:

    {"code": 3001, "message": "..."}                          domain errors
    {"code": 2002, "message": "...", "invalid_fields": [...]}  ValidationHTTPError
    {"message": "..."}                                         bare string errors

The third shape has no ``code``. That is upstream behaviour, not an oversight —
do not "helpfully" add one. ``i18n_params`` is ``omitempty`` on the Go struct, so
it is absent rather than null when there is nothing to interpolate.

The one place we deliberately diverge from "whatever Echo did" is 401, which is
pinned to code 11 for every auth failure (pkg/routes/api_tokens.go:37).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from calton.core.error_codes import ERROR_CODES, ErrorCodeSpec
from calton.permissions.project import CyclicHierarchyError

logger = logging.getLogger("calton.errors")

# pkg/routes/api_tokens.go:37. Not part of the generated table — it lives in the
# routes package rather than in either error.go.
INVALID_TOKEN_CODE = 11
INVALID_TOKEN_MESSAGE = "missing, malformed, expired or otherwise invalid token provided"

# error_handler.go:55-56 seeds the message with http.StatusText(500), and :115-119
# wraps a bare string as {"message": ...}. So an unhandled error is JSON, not the
# plain text Starlette would otherwise send.
INTERNAL_SERVER_ERROR_MESSAGE = "Internal Server Error"


class CaltonError(Exception):
    """A domain error rendered as ``{code, message, i18n_params?}``."""

    def __init__(
        self,
        code: int,
        message: str,
        http_status: int,
        i18n_params: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.i18n_params = i18n_params or {}

    @classmethod
    def from_name(cls, key: str, /, **values: Any) -> CaltonError:
        """Build an error from its table entry, e.g. ``"models.ErrProjectDoesNotExist"``.

        Keyword arguments fill the message template and, where the entry declares
        them, the ``i18n_params`` map. The key is positional-only because template
        fields are free to be called ``key``, ``spec`` or anything else — several
        are called ``name``.
        """
        return cls.from_spec(ERROR_CODES[key], **values)

    @classmethod
    def from_spec(cls, spec: ErrorCodeSpec, /, **values: Any) -> CaltonError:
        missing = [field for field in spec.template_fields if field not in values]
        if missing:
            raise ValueError(f"{spec.name} needs template fields: {', '.join(missing)}")

        return cls(
            code=spec.code,
            message=spec.message.format(**values),
            http_status=spec.http_status,
            i18n_params={key: str(values[field]) for key, field in spec.i18n_params},
        )

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.i18n_params:
            body["i18n_params"] = self.i18n_params
        return body


class ValidationError(CaltonError):
    """Echoes Go's ``ValidationHTTPError``: exactly code, message, invalid_fields.

    Note the 412 status — ``InvalidFieldError`` uses StatusPreconditionFailed even
    though the underlying ErrInvalidData is a 400 (pkg/models/error.go).
    """

    def __init__(self, invalid_fields: list[str], message: str = "Invalid Data") -> None:
        spec = ERROR_CODES["models.ErrInvalidData"]
        super().__init__(code=spec.code, message=message, http_status=412)
        self.invalid_fields = invalid_fields

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "invalid_fields": self.invalid_fields}


class UnauthorizedError(CaltonError):
    """The auth middleware's 401, which is always code 11.

    Note this is not the same as "every 401 is code 11". A domain error carrying
    a 401 status — ``user.ErrInvalidUserContext``, code 1027 — renders its own
    code, because it comes out of the error table rather than the middleware.
    See ``test_a_401_domain_error_keeps_its_own_code``.
    """

    def __init__(self) -> None:
        super().__init__(code=INVALID_TOKEN_CODE, message=INVALID_TOKEN_MESSAGE, http_status=401)


#: What upstream actually puts on the wire for **every** bind failure, path parameter or
#: body. ``ErrInvalidModel.Error()`` is ``"Invalid model provided: <the bind error>"`` and
#: Echo's binder reports ``echo.ErrBadRequest``, whose text is "Bad Request" — so the
#: interpolation is a constant in practice.
#:
#: Measured on seven endpoints and three kinds of malformed body (wrong scalar type,
#: wrong type on a nested field, syntactically invalid JSON): the string never varies.
#: ⚠️ This used to read "Invalid model provided." on the body path while the path-parameter
#: path (``crud_router._INVALID_MODEL_MESSAGE``) had the measured wording, so the two
#: disagreed. No corpus case caught it because every 2004 case in the corpus is a *path*
#: parameter failure — the body half had no coverage at all. Found by T30.
INVALID_MODEL_MESSAGE = "Invalid model provided: Bad Request"


class ModelBindError(CaltonError):
    """Body could not be bound at all — malformed JSON, wrong type at the root.

    Upstream separates this from validation: ``ctx.Bind`` failing yields
    ``models.ErrInvalidModel`` (code 2004, **400**), and only a bound-but-invalid
    struct reaches ``ctx.Validate`` and the 412/2002 ValidationHTTPError
    (create.go:35-48). Collapsing the two reports a parse position such as "1" in
    ``invalid_fields`` where a client expects a field name.
    """

    def __init__(self, message: str = INVALID_MODEL_MESSAGE) -> None:
        spec = ERROR_CODES["models.ErrInvalidModel"]
        super().__init__(code=spec.code, message=message, http_status=400)


# HTTPErrorWithDetails (web.go:54-57) adds a `details` field to the standard
# body. Nothing in Phase 1's 68 operations returns it — it is used by admin and
# migration endpoints, which are all Phase 2+ — so it is deliberately not
# implemented here rather than left as a half-built variant. Add it with the
# endpoint that first needs it.
HTTP_ERROR_WITH_DETAILS_SUPPORTED = False


class EchoStringError(Exception):
    """A bare string error, rendered as ``{"message": ...}`` with no code.

    Used where the Go side calls ``echo.NewHTTPError(status, "some text")``, such
    as the pagination guards in pkg/web/handler/read_all.go.
    """

    def __init__(self, http_status: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.message = message

    def body(self) -> dict[str, Any]:
        return {"message": self.message}


def allow_header_for(request: Request) -> str | None:
    """``Allow`` for a 405, spelled the way echo spells it.

    Starlette builds its own and it is wrong on both counts: it is derived from a
    ``set`` (so the order is not the registration order), and it lists the ``HEAD``
    it auto-adds to every ``GET`` while omitting ``OPTIONS``. Measured against the
    reference server on ``/api/v1/token/test``: ``OPTIONS, GET, POST`` — ``HEAD``
    absent (it is itself a 405 there) and ``OPTIONS`` first.

    So this recomputes it from the app's own routing table: ``OPTIONS`` first,
    then the real methods in the order their routes were registered. Recomputing
    beats hardcoding the one string the corpus asserts — a hardcoded value would
    still say ``POST`` on the day somebody deletes the ``POST`` route, which is
    precisely the regression the header exists to expose.

    The methods come from ``app.openapi()["paths"]`` rather than from a walk of
    ``app.routes``: FastAPI now wraps every ``include_router`` in a private
    ``_IncludedRouter`` whose children are reachable only through internal
    ``effective_candidates()`` bookkeeping, and a header that has to be right is
    not a good place to depend on that. The generated document is also the same
    source the wiring checks use, so "which methods exist" has one answer here,
    and its key order is registration order.

    Returns ``None`` when the path is not in the document at all, which is a 404
    rather than a 405 and carries no ``Allow``.
    """
    from calton.auth.deps import route_template

    template = route_template(request)
    if template is None:
        return None

    operations = request.app.openapi()["paths"].get(template)
    if not operations:
        return None

    methods = [m.upper() for m in operations if m.lower() not in ("head", "options")]
    if not methods:
        return None
    return ", ".join(["OPTIONS", *methods])


def _respond(
    request: Request,
    http_status: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Response:
    # HEAD carries the status but no body (pkg/routes/error_handler.go:112).
    if request.method == "HEAD":
        return Response(status_code=http_status, headers=headers)
    return JSONResponse(status_code=http_status, content=body, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    """Wire Calton's error types onto an app so they render the v1 bodies."""

    @app.exception_handler(CaltonError)
    def _handle_calton_error(request: Request, exc: CaltonError) -> Response:
        return _respond(request, exc.http_status, exc.body())

    @app.exception_handler(EchoStringError)
    def _handle_echo_string_error(request: Request, exc: EchoStringError) -> Response:
        return _respond(request, exc.http_status, exc.body())

    @app.exception_handler(RequestValidationError)
    def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> Response:
        # Two different upstream errors hide behind FastAPI's one exception.
        # A body that could not be bound at all is ErrInvalidModel (400/2004);
        # only a bound-but-invalid body is the 412/2002 ValidationHTTPError.
        if is_bind_failure(exc):
            return _respond(request, 400, ModelBindError().body())
        return _respond(request, 412, ValidationError(invalid_fields_of(exc)).body())

    @app.exception_handler(CyclicHierarchyError)
    def _handle_cyclic_hierarchy(request: Request, exc: CyclicHierarchyError) -> Response:
        # Corrupt data, not a business error, so it gets no error code of its own
        # (see the class docstring in permissions/project.py). The response is the
        # ordinary 500 fallback; the diagnosis — project id and depth — goes to the
        # log only, because putting it in the body would leak hierarchy structure
        # to a caller who may not be allowed to see it.
        #
        # Deliberately NOT a 403: a denial here is indistinguishable from a real
        # one, so data corruption would be misdiagnosed as a permissions problem.
        logger.error("cyclic project hierarchy: project=%s depth=%s", exc.project_id, exc.depth)
        return _respond(request, 500, {"message": INTERNAL_SERVER_ERROR_MESSAGE})

    @app.exception_handler(Exception)
    def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
        # Without this, an unhandled exception escapes as Starlette's plain-text
        # "Internal Server Error" — not even JSON, so MCP clients throw inside
        # JSON.parse rather than surfacing the error. Echo's recover middleware
        # produces a JSON body here; error_handler.go:55-56 sets the message to
        # http.StatusText(500) and :115-119 wraps a bare string as {"message":...}.
        return _respond(request, 500, {"message": INTERNAL_SERVER_ERROR_MESSAGE})

    @app.exception_handler(StarletteHTTPException)
    def _handle_starlette_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        # 401 is pinned to code 11 whatever raised it; everything else falls back
        # to the bare-string shape rather than FastAPI's {"detail": ...}.
        if exc.status_code == 401:
            return _respond(request, 401, UnauthorizedError().body())
        # `exc.headers` was being dropped, and on a 405 that is where Starlette puts
        # `Allow`. Upstream always sends it, and it is the only part of a 405 that says
        # *which* methods exist — so dropping it turned every "wrong set of methods
        # registered" bug into an invisible one.
        #
        # ⚠️ But Starlette's own value is not upstream's, and taking it is the obvious
        # move: it raises the 405 from the first route that matched the path with the
        # wrong method, and each verb is a separate Route object, so that header names
        # exactly one verb — `Allow: POST` where upstream sends four. A header listing
        # one of the four is worse than none: it tells a client that feature-detects
        # that GET and DELETE are unavailable. Hence the recomputation.
        headers = dict(exc.headers or {})
        if exc.status_code == 405:
            allow = allow_header_for(request)
            headers.pop("Allow", None)
            if allow is not None:
                headers["Allow"] = allow
        return _respond(request, exc.status_code, {"message": exc.detail}, headers or None)


# The dividing line upstream is *which layer refused*, not how severe the problem
# is. ctx.Bind hands the body to encoding/json; anything encoding/json rejects —
# malformed syntax, a wrong root type, and equally a field whose JSON type does
# not match the struct field — is ErrInvalidModel, 400/2004. Only a body that
# decoded cleanly reaches ctx.Validate, and only that layer produces the 412/2002
# ValidationHTTPError.
#
# So this is written as an allow-list of validator-layer errors, with everything
# else falling to Bind. That direction matters: Pydantic gains new error types
# over time, and an unknown type is far more likely to be a decode problem than a
# business-rule problem. Defaulting unknowns to the Bind side puts new cases on
# the side that is right more often, instead of silently mis-classifying them.
VALIDATOR_ERROR_TYPES = frozenset(
    {
        "missing",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_short",
        "too_long",
        # ⚠️ Pydantic reports length violations under **two different type names**, and
        # only one of them was here. ``too_short``/``too_long`` are what a *collection*
        # constraint emits; a ``str`` with ``min_length`` emits ``string_too_short``.
        # Missing them routed every "required field was empty" through the bind branch,
        # so ``PUT /projects {"title": ""}`` answered 400/2004 where the reference server
        # answers 412/2002 with ``invalid_fields`` — the code the frontend draws
        # field-level errors from. Found by the first endpoint to use a string
        # ``min_length``; it would have hit every resource with a required title.
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "value_error",
    }
)


def is_bind_failure(exc: RequestValidationError) -> bool:
    """Whether the body failed to decode rather than failing validation.

    True for anything encoding/json would have refused, which includes
    field-level type mismatches such as ``{"priority": "high"}``.
    """
    errors = exc.errors()
    if not errors:
        return False
    return any(error.get("type") not in VALIDATOR_ERROR_TYPES for error in errors)


#: Pydantic prefixes a validator's own message with this. Stripped so the wording that
#: goes on the wire is exactly the one measured from upstream.
_VALUE_ERROR_PREFIX = "Value error, "


def invalid_fields_of(exc: RequestValidationError) -> list[str]:
    """``"<field>: <reason>"`` entries, matching govalidator's wording.

    ``loc`` is ("body", "title") or ("query", "per_page"); the leading location marker is
    dropped so the client sees the field name it sent.

    A field annotated with :func:`~calton.db.types.GoValid` raises with upstream's own
    text, and that text is appended after the field name — ``"title: non zero value
    required"``. A field without one contributes its bare name, as everything here used
    to: that is a **known shortfall, not a design**, and it is why the parity cases for
    those resources are still red. Each write schema's owner adds the tag their Go struct
    carries; the tag text must be copied, not paraphrased.

    ⚠️ **Order is not meaningful — compare these as a set.** govalidator collects into a
    map, and Go randomises where a map walk starts, so the array comes back rotated by a
    random amount: sampling ``PUT /tokens`` 40 times gave the declaration order 29 times
    and two rotations of it 11 times between them. Five consecutive samples had shown a
    stable order, which is exactly how this got recorded as "declaration order" in the
    first place.
    """
    fields: list[str] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ())]
        if len(location) > 1:
            location = location[1:]
        field = ".".join(location)
        if not field:
            continue

        message = str(error.get("msg", ""))
        if error.get("type") == "value_error" and message.startswith(_VALUE_ERROR_PREFIX):
            entry = f"{field}: {message.removeprefix(_VALUE_ERROR_PREFIX)}"
        else:
            entry = field

        if entry not in fields:
            fields.append(entry)
    return fields
