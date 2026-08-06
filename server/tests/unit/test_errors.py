"""T04 — error response bodies.

Every assertion here is a byte-for-byte contract with the Go implementation's
central handler (pkg/routes/error_handler.go). Where upstream is inconsistent we
copy the inconsistency rather than tidy it up.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from calton.core.errors import (
    HTTP_ERROR_WITH_DETAILS_SUPPORTED,
    INTERNAL_SERVER_ERROR_MESSAGE,
    INVALID_MODEL_MESSAGE,
    INVALID_TOKEN_CODE,
    INVALID_TOKEN_MESSAGE,
    CaltonError,
    EchoStringError,
    ModelBindError,
    UnauthorizedError,
    ValidationError,
    register_exception_handlers,
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/project")
    def project() -> None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")

    @app.get("/timezone")
    def timezone() -> None:
        raise CaltonError.from_name("models.ErrInvalidTimezone", name="Foo/Bar")

    @app.get("/task-field")
    def task_field() -> None:
        raise CaltonError.from_name("models.ErrInvalidTaskField", task_field="foo")

    @app.get("/forbidden")
    def forbidden() -> None:
        raise CaltonError.from_name("models.ErrGenericForbidden")

    @app.get("/validation")
    def validation() -> None:
        raise ValidationError(["expand"])

    @app.get("/unauthorized")
    def unauthorized() -> None:
        raise UnauthorizedError()

    @app.get("/echo")
    def echo() -> None:
        raise EchoStringError(400, "Page number cannot be negative.")

    @app.api_route("/head-project", methods=["GET", "HEAD"])
    def head_project() -> None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")

    @app.get("/needs-query")
    def needs_query(per_page: int) -> dict[str, int]:
        return {"per_page": per_page}

    @app.get("/starlette-401")
    def starlette_401() -> None:
        raise StarletteHTTPException(status_code=401, detail="Unauthorized")

    @app.get("/starlette-404")
    def starlette_404() -> None:
        raise StarletteHTTPException(status_code=404, detail="Not Found")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("something nobody handled")

    @app.get("/invalid-user-context")
    def invalid_user_context() -> None:
        raise CaltonError.from_name("user.ErrInvalidUserContext")

    class Body(BaseModel):
        title: str
        priority: int = 0

    @app.post("/bind")
    def bind(body: Body) -> dict[str, str]:
        return {"title": body.title}

    return TestClient(app, raise_server_exceptions=False)


def test_domain_error_is_code_and_message_only(client: TestClient) -> None:
    resp = client.get("/project")
    assert resp.status_code == 404
    assert resp.json() == {"code": 3001, "message": "This project does not exist."}


def test_i18n_params_is_omitted_when_empty(client: TestClient) -> None:
    resp = client.get("/task-field")
    assert resp.status_code == 400
    assert resp.json() == {"code": 4016, "message": "The task field 'foo' is invalid."}
    assert "i18n_params" not in resp.json()


def test_i18n_params_is_present_when_the_error_carries_them(client: TestClient) -> None:
    resp = client.get("/timezone")
    assert resp.status_code == 400
    assert resp.json() == {
        "code": 2003,
        "message": "The timezone 'Foo/Bar' is invalid",
        "i18n_params": {"timezone": "Foo/Bar"},
    }


def test_generic_forbidden(client: TestClient) -> None:
    resp = client.get("/forbidden")
    assert resp.status_code == 403
    assert resp.json() == {"code": 1, "message": "You're not allowed to do this."}


def test_validation_error_is_exactly_code_message_invalid_fields(client: TestClient) -> None:
    resp = client.get("/validation")
    assert resp.status_code == 412
    assert resp.json() == {"code": 2002, "message": "Invalid Data", "invalid_fields": ["expand"]}


def test_unauthorized_is_always_code_11(client: TestClient) -> None:
    resp = client.get("/unauthorized")
    assert resp.status_code == 401
    assert resp.json() == {"code": INVALID_TOKEN_CODE, "message": INVALID_TOKEN_MESSAGE}
    assert INVALID_TOKEN_CODE == 11
    assert (
        INVALID_TOKEN_MESSAGE == "missing, malformed, expired or otherwise invalid token provided"
    )


def test_plain_string_error_has_a_message_but_no_code(client: TestClient) -> None:
    """Upstream's handler wraps bare strings as {"message": ...}. Do not "helpfully" add a code."""
    resp = client.get("/echo")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Page number cannot be negative."}
    assert "code" not in resp.json()


def test_head_request_returns_an_empty_body(client: TestClient) -> None:
    resp = client.head("/head-project")
    assert resp.status_code == 404
    assert resp.content == b""


def test_get_on_the_same_route_still_has_a_body(client: TestClient) -> None:
    resp = client.get("/head-project")
    assert resp.status_code == 404
    assert resp.json() == {"code": 3001, "message": "This project does not exist."}


def test_from_name_rejects_an_unknown_constant() -> None:
    with pytest.raises(KeyError):
        CaltonError.from_name("models.ErrNoSuchThing")


def test_from_name_rejects_missing_template_fields() -> None:
    with pytest.raises(ValueError, match="task_field"):
        CaltonError.from_name("models.ErrInvalidTaskField")


def test_i18n_params_are_rendered_from_the_same_values_as_the_message() -> None:
    err = CaltonError.from_name("models.ErrInvalidTimezone", name="Europe/Nowhere")
    assert err.message == "The timezone 'Europe/Nowhere' is invalid"
    assert err.i18n_params == {"timezone": "Europe/Nowhere"}


def test_a_template_field_may_be_called_name() -> None:
    """The lookup key is positional-only, so it cannot collide with a field name.

    ErrInvalidTimezone's field really is called `name`; a keyword `name=` parameter
    on from_name() would make it unraisable.
    """
    err = CaltonError.from_name("models.ErrInvalidTimezone", name="Foo/Bar")
    assert err.code == 2003


def test_fastapi_validation_failures_use_the_v1_shape_not_detail(client: TestClient) -> None:
    resp = client.get("/needs-query")
    assert resp.status_code == 412
    assert resp.json() == {"code": 2002, "message": "Invalid Data", "invalid_fields": ["per_page"]}
    assert "detail" not in resp.json()


def test_a_starlette_401_from_anywhere_becomes_code_11(client: TestClient) -> None:
    resp = client.get("/starlette-401")
    assert resp.status_code == 401
    assert resp.json() == {"code": 11, "message": INVALID_TOKEN_MESSAGE}


def test_other_starlette_errors_fall_back_to_the_bare_string_shape(client: TestClient) -> None:
    resp = client.get("/starlette-404")
    assert resp.status_code == 404
    assert resp.json() == {"message": "Not Found"}
    assert "detail" not in resp.json()


def test_unrouted_paths_do_not_leak_fastapis_detail_body(client: TestClient) -> None:
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    assert "detail" not in resp.json()


# --- the fallback handler (item ③) ------------------------------------------


def test_an_unhandled_exception_is_json_not_plain_text(client: TestClient) -> None:
    """Without a catch-all, Starlette sends the literal bytes "Internal Server
    Error" with no JSON at all, and MCP clients throw inside JSON.parse rather
    than reporting the error."""
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"message": INTERNAL_SERVER_ERROR_MESSAGE}


def test_the_fallback_body_has_no_code_field(client: TestClient) -> None:
    """error_handler.go:115-119 wraps a bare string, and the bare-string shape
    carries no code."""
    assert "code" not in client.get("/boom").json()


def test_the_fallback_still_honours_head(client: TestClient) -> None:
    assert client.request("HEAD", "/boom").content == b""


# --- a 401 that is not the middleware's (item ④) -----------------------------


def test_a_401_domain_error_keeps_its_own_code(client: TestClient) -> None:
    """user.ErrInvalidUserContext is a 401 carrying code 1027, not 11. "401 is
    always code 11" holds for the auth middleware, not for every 401 body.

    TODO-W2: confirm against the real Go server once the parity harness is up
    (T10). If Go turns out to normalise this to 11, change the assertion — but
    this path must never again be untested.
    """
    resp = client.get("/invalid-user-context")
    assert resp.status_code == 401
    assert resp.json()["code"] == 1027
    assert resp.json()["code"] != INVALID_TOKEN_CODE


def test_the_middleware_401_is_still_code_11(client: TestClient) -> None:
    """The contrast that makes the rule above legible."""
    assert client.get("/unauthorized").json()["code"] == 11


# --- bind failure versus validation failure (item ⑤) ------------------------


def test_malformed_json_is_400_with_code_2004_not_412(client: TestClient) -> None:
    """ctx.Bind failing is ErrInvalidModel (400/2004); only a bound-but-invalid
    struct reaches the validator and the 412/2002 shape. Reporting 412 here also
    put a parse position ("1") in invalid_fields where a field name belongs."""
    resp = client.post("/bind", content=b"{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json() == {"code": 2004, "message": INVALID_MODEL_MESSAGE}
    assert "invalid_fields" not in resp.json()


def test_a_json_array_where_an_object_belongs_is_also_a_bind_failure(client: TestClient) -> None:
    resp = client.post("/bind", json=[1, 2, 3])
    assert resp.status_code == 400
    assert resp.json()["code"] == 2004


def test_a_missing_field_is_412_with_the_field_name(client: TestClient) -> None:
    """This one did bind; it failed validation, so it keeps the 412/2002 shape."""
    resp = client.post("/bind", json={"priority": 3})
    assert resp.status_code == 412
    assert resp.json() == {"code": 2002, "message": "Invalid Data", "invalid_fields": ["title"]}


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "x", "priority": "high"},  # int_parsing
        {"title": 123},  # string_type
        {"title": "x", "priority": 1.5},  # int_from_float
    ],
)
def test_a_field_level_type_mismatch_is_a_bind_failure(payload: dict[str, object]) -> None:
    """The dividing line upstream is which layer refused, not how severe it is.

    encoding/json rejects a field whose JSON type does not match the struct
    field, so ctx.Bind fails and the answer is 400/2004 — the body never reaches
    ctx.Validate and so can never produce the 412/2002 shape. These three
    previously came back 412.
    """
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        model_config = ConfigDict(strict=True)

        title: str = ""
        priority: int = 0

    @app.post("/strict")
    def strict(body: Body) -> dict[str, object]:
        return body.model_dump()

    resp = TestClient(app, raise_server_exceptions=False).post("/strict", json=payload)
    assert resp.status_code == 400
    assert resp.json() == {"code": 2004, "message": INVALID_MODEL_MESSAGE}


def test_unknown_pydantic_error_types_default_to_the_bind_side() -> None:
    """The classifier is an allow-list of validator errors, so a Pydantic version
    that adds a new type puts it on the side that is right more often."""
    from calton.core.errors import VALIDATOR_ERROR_TYPES, is_bind_failure

    class FakeExc:
        def errors(self) -> list[dict[str, str]]:
            return [{"type": "some_future_pydantic_error"}]

    assert is_bind_failure(FakeExc())  # type: ignore[arg-type]
    assert "missing" in VALIDATOR_ERROR_TYPES


def test_model_bind_error_defaults() -> None:
    err = ModelBindError()
    assert err.code == 2004
    assert err.http_status == 400


# --- HTTPErrorWithDetails is deliberately absent (item ⑦) -------------------


def test_http_error_with_details_is_registered_as_unimplemented() -> None:
    """Phase 1's 68 operations never return the `details` variant. Recorded as a
    decision so it is not mistaken for an oversight."""
    assert HTTP_ERROR_WITH_DETAILS_SUPPORTED is False
