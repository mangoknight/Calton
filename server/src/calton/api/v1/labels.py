"""Label endpoints — the resource itself, plus the three that hang off a task.

``/labels`` and ``/labels/{label}`` are mounted through :class:`~calton.core.crud_router.
CRUDRouter`: they are the shape it registers, and everything that makes them a wire
contract (the inverted verbs, ``x-max-permission``, the delete body) is therefore shared
with the other 58 endpoints rather than respelled here.

The three task-label routes are hand-written, because none of them returns the resource:

* ``PUT /tasks/{task}/labels`` answers ``{label_id, created}`` — two keys, neither the
  label nor the task
* ``POST /tasks/{task}/labels/bulk`` echoes the **request**, unhydrated, in the order it
  was submitted, while the database ends up holding something else
* ``GET /tasks/{task}/labels`` is the only one that returns labels, and it is a
  paginated collection hanging off another resource's id

A generic pipeline that serialised the affected model would produce a hydrated label for
the first two, which is more useful and is not what any existing client receives.

**No policy decision is made in this module.** The order in which existence and
permission are tested is what produces 8002 against 0 against 4002 against 8003, and that
order lives in ``services/label_service`` next to the measurements that justify it. Here
we only bind it to HTTP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session, object_session

from calton.core.crud_router import CRUDRouter, deleted_response, path_param_as_id
from calton.core.errors import UnauthorizedError
from calton.core.pagination import Paginator, paginated_response
from calton.db.session import get_db
from calton.models import Label
from calton.schemas.label import LabelAttached, LabelBulk, LabelRead, LabelWrite
from calton.schemas.message import Message
from calton.services import label_service
from calton.services.label_crud import LabelPolicy, LabelService

#: ``/labels`` and ``/labels/{label}``. Exposed so the app can register it with
#: ``route_registry`` from the same object it mounts, which is what stops the routing
#: table and the API-token permission table from disagreeing.
LABEL_PREFIX = "/labels"
LABEL_ITEM_PARAM = "label"


def _auth_user_id(request: Request) -> int:
    """The authenticated user's id.

    ``request.state.auth`` is populated by the JWT middleware (T14) and the API token
    middleware (T15). Until one of them runs there is no subject, and the correct answer
    is the middleware's 401 — never a silent fallback to some default user, which would
    make every endpoint here publicly writable.
    """
    auth = getattr(request.state, "auth", None)
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def _serialize(session: Session, label: Label) -> dict[str, object]:
    return label_service.label_view(session, label).model_dump(mode="json")


def _serialize_for_crud(label: Label, session: Session, in_collection: bool) -> dict[str, object]:
    """CRUDRouter's serializer hook.

    ⚠️ The signature grew on another branch in the same window this was written — the
    router now passes the session and a collection flag. Both sides merged cleanly and
    the result was a TypeError inside the handler, i.e. a 500 on every label route. The
    session argument is now used directly; ``object_session`` below stays only as the
    fallback for the fixtures that call this helper with a detached row.

    ``created_by`` is a join, so the row alone is not enough — and the default serializer
    (``read_schema.model_validate(model, from_attributes=True)``) would silently emit
    ``created_by: null`` for every label, since the attribute does not exist on the ORM
    class at all. That is a null the corpus catches (``label.read_one.ok`` compares the
    embedded user byte for byte), but it would look like a missing relationship rather
    than a missing serializer.

    ``object_session`` recovers the request's session from the instance, which is the same
    one the policy and service ran in — the router flushes before serialising, so a
    freshly created label is attached by the time this runs.
    """
    resolved = session or object_session(label)
    if resolved is None:  # pragma: no cover - the router always hands us attached rows
        raise RuntimeError("label is detached; cannot resolve created_by")
    return _serialize(resolved, label)


def build_crud_router() -> CRUDRouter[Label, LabelRead, LabelWrite]:
    """The six CRUD routes, as a CRUDRouter so it can also be handed to route_registry."""
    return CRUDRouter(
        prefix=LABEL_PREFIX,
        item_param=LABEL_ITEM_PARAM,
        service=LabelService(),
        policy=LabelPolicy(),
        read_schema=LabelRead,
        write_schema=LabelWrite,
        serialize=_serialize_for_crud,
    )


def build_router() -> APIRouter:
    """The three task-label routes. ``/labels`` itself comes from :func:`build_crud_router`."""
    router = APIRouter()

    @router.get("/tasks/{task}/labels", response_model=list[LabelRead])
    def read_task_labels(
        request: Request,
        # Declared so the operation documents its path parameter: FastAPI emits no
        # `parameters` block for an id read off `request`, and schemathesis refuses to
        # fuzz such an operation (`InvalidSchema: Path parameter ... is not defined`).
        # `str`, never `int` — an int annotation answers 422 where upstream answers
        # 400/2004. See core.crud_router.path_parameter_block.
        task: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        """Every label on the task, id ascending, whoever attached it.

        Read permission on the **task**, not on each label: a collaborator's label on a
        task you can see is listed. Filtering this by ``created_by`` would make labels
        other people attached vanish from your view of a shared task without any error.

        Note the two different refusals reachable here, both measured: a task you cannot
        see is 403/**4005** (the task's own code, from the outer check), while the same
        task refused by ``bulk`` below is 403/**0**. Read and write paths do not share an
        error vocabulary even for the same resource.
        """
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)

        label_service.load_task_for_read(session, user_id, task_id)
        labels = label_service.labels_on_task(session, task_id)

        # An empty task answers `[]` here — while the same emptiness inside
        # `GET /tasks/{id}` serialises as `labels: null`. One "empty", two shapes, and a
        # single shared model would quietly unify them and break the frontend's
        # `task.labels === null` branch. See tasklabel.read_all.empty_is_array_not_null.
        return paginated_response(
            [_serialize(session, label) for label in labels],
            total_items=len(labels),
            per_page=paginator.per_page,
            result_count=len(labels),
        )

    @router.put("/tasks/{task}/labels", status_code=201, response_model=LabelAttached)
    def add_task_label(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        body: LabelAttached,
        session: Session = Depends(get_db),
    ) -> Response:
        """Attach one label. Answers ``{label_id, created}`` and nothing else.

        ``LabelAttached`` is both the request and the response model, which is what
        upstream binds too. It matters that ``label_id`` has a **default of 0** rather
        than being required: an empty body must reach the service and come back 403, where
        a required field would make FastAPI answer 422 — a status this API never emits.
        """
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)

        attachment = label_service.attach_label(
            session, user_id, task_id=task_id, label_id=body.label_id
        )
        # `get_db` closes the session without committing, so a service that only flushes
        # has its work discarded after the response has already been built and sent. See
        # the warning at the top of core/crud_router.
        session.commit()
        return JSONResponse(
            status_code=201,
            content=LabelAttached(
                label_id=attachment.label_id, created=attachment.created
            ).model_dump(mode="json"),
        )

    @router.delete("/tasks/{task}/labels/{label}", response_model=Message)
    def remove_task_label(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        label: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        """Detach one label. A label that is not attached answers 403, not 404."""
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)
        label_id = path_param_as_id(label)

        label_service.detach_label(session, user_id, task_id=task_id, label_id=label_id)
        session.commit()
        return deleted_response()

    @router.post("/tasks/{task}/labels/bulk", status_code=201, response_model=LabelBulk)
    def replace_task_labels(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        body: LabelBulk,
        session: Session = Depends(get_db),
    ) -> Response:
        """Replace the task's whole label set, and echo the request back unchanged.

        The response is built from ``body``, never re-read from the database. That is not
        an optimisation — it is the observable contract: titles come back empty,
        ``created_by`` null, timestamps zero, and the order is the request's rather than
        the id order ``GET`` uses. Hydrating it here would be an improvement no client
        asked for and a byte-level regression.
        """
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)

        label_service.replace_labels(
            session, user_id, task_id=task_id, label_ids=[entry.id for entry in body.labels]
        )
        session.commit()
        return JSONResponse(status_code=201, content=body.model_dump(mode="json"))

    return router


#: (method, path) for everything this module registers, so route_registry and the app can
#: never disagree about which routes exist. The ``/labels`` routes are **not** listed:
#: they come from the CRUDRouter's own ``registered_actions()``, which is the single
#: source both the mount and the registry read.
#:
#: ``/tasks/{task}/labels`` files under the group ``tasks_labels`` and the bulk route
#: under ``tasks_labels.update_bulk`` — both fall out of the generic naming rules, so
#: neither needs a special case. Getting a group name wrong here does not break routing;
#: it makes every API-token call against these three routes 403 while JWT calls keep
#: working, which reads like anything except a permissions problem.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/tasks/{task}/labels"),
    ("PUT", "/api/v1/tasks/{task}/labels"),
    ("DELETE", "/api/v1/tasks/{task}/labels/{label}"),
    ("POST", "/api/v1/tasks/{task}/labels/bulk"),
)
