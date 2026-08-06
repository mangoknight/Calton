"""The label resource through the CRUDRouter pipeline.

The pipeline answers 403 whenever the policy refuses, so the policy's answers decide
which status a request gets. These tests pin the mapping — in particular the case that
looks like a hole and is not.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.core.policy import ForbiddenError
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.models import Label, Project, Task, User
from calton.schemas.label import LabelWrite
from calton.services.label_crud import LabelPolicy, LabelService
from calton.services.label_service import LabelDoesNotExistError

ALICE, BOB = 900, 901


@pytest.fixture
def session() -> Iterator[Session]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as opened:
        opened.add_all(
            [
                User(id=ALICE, username="alice"),
                User(id=BOB, username="bob"),
                Project(id=950, title="p", owner_id=ALICE),
                Task(id=950, title="t", project_id=950, index=1, created_by_id=ALICE),
                Label(id=950, title="mine", created_by_id=ALICE),
                Label(id=960, title="bob's", created_by_id=BOB),
            ]
        )
        opened.commit()
        yield opened


@pytest.fixture
def policy() -> LabelPolicy:
    return LabelPolicy()


@pytest.fixture
def service() -> LabelService:
    return LabelService()


class TestWriteStatusMapping:
    """403 versus 404 is decided by whether the policy refuses or the service raises."""

    def test_a_missing_label_passes_the_policy_so_the_service_can_404(
        self, session: Session, policy: LabelPolicy, service: LabelService
    ) -> None:
        """The counterintuitive one. If the policy refused here the pipeline would answer
        403, and the corpus requires 404/8002."""
        assert policy.can_update(session, auth=ALICE, label=9999)

        with pytest.raises(LabelDoesNotExistError) as raised:
            service.update(session, LabelWrite(), auth=ALICE, label=9999)
        assert raised.value.code == 8002

    def test_someone_elses_label_is_refused_by_the_policy(
        self, session: Session, policy: LabelPolicy
    ) -> None:
        """An existing label owned by another user is a 403, not a 404."""
        assert not policy.can_update(session, auth=ALICE, label=960)

    def test_the_owner_passes(self, session: Session, policy: LabelPolicy) -> None:
        assert policy.can_update(session, auth=ALICE, label=950)

    def test_delete_uses_the_same_rule(self, session: Session, policy: LabelPolicy) -> None:
        assert policy.can_delete(session, auth=ALICE, label=9999)
        assert not policy.can_delete(session, auth=ALICE, label=960)
        assert policy.can_delete(session, auth=ALICE, label=950)


class TestReadStatusMapping:
    def test_missing_and_invisible_are_both_refused(
        self, session: Session, policy: LabelPolicy
    ) -> None:
        assert policy.can_read(session, auth=ALICE, label=9999) == (False, 0)
        assert policy.can_read(session, auth=ALICE, label=960) == (False, 0)

    def test_a_visible_label_is_allowed(self, session: Session, policy: LabelPolicy) -> None:
        allowed, _ = policy.can_read(session, auth=ALICE, label=950)
        assert allowed

    def test_reading_a_missing_label_raises_forbidden_not_missing(
        self, session: Session, service: LabelService
    ) -> None:
        with pytest.raises(ForbiddenError):
            service.read_one(session, auth=ALICE, label=9999)


class TestCreate:
    def test_anyone_may_create(self, session: Session, policy: LabelPolicy) -> None:
        assert policy.can_create(session, auth=ALICE)

    def test_an_empty_body_creates_a_label(self, session: Session, service: LabelService) -> None:
        """No validation, per the corpus: PUT /labels with {} is a 201."""
        label = service.create(session, LabelWrite(), auth=ALICE)

        assert label.id is not None
        assert label.title == ""
        assert label.created_by_id == ALICE

    def test_a_whitespace_title_is_stored_unchanged(
        self, session: Session, service: LabelService
    ) -> None:
        assert service.create(session, LabelWrite(title="   "), auth=ALICE).title == "   "


class TestUpdateReplacesWholesale:
    def test_an_omitted_field_is_reset_not_preserved(
        self, session: Session, service: LabelService
    ) -> None:
        """POST is a full replacement, so a body without description clears it."""
        service.update(session, LabelWrite(title="renamed"), auth=ALICE, label=950)

        label = service.read_one(session, auth=ALICE, label=950)
        assert label.title == "renamed"
        assert label.description == ""


class TestReadAll:
    def test_it_reports_counts_alongside_the_rows(
        self, session: Session, service: LabelService
    ) -> None:
        labels, result_count, total = service.read_all(session, auth=ALICE)

        assert [label.id for label in labels] == [950]
        assert result_count == 1
        assert total == 1

    def test_it_is_scoped_per_user_without_a_permission_gate(
        self, session: Session, service: LabelService
    ) -> None:
        """read_all has no gate above it; the query itself does the scoping."""
        assert [label.id for label in service.read_all(session, auth=BOB)[0]] == [960]


class TestTheyCanActuallyBeBoundToCRUDRouter:
    """The cell every other test in this file misses.

    Each test above constructs ``LabelPolicy()`` / ``LabelService()`` itself and calls the
    methods directly. That makes the whole file **blind to the calling convention**: an
    earlier version of these classes took a ``session_for`` callable in the constructor and
    omitted ``session`` from every method, and this file was entirely green against it —
    because it built the objects the same wrong way it called them. Nothing here could
    fail, and no ``CRUDRouter`` was ever constructed from them, so the production path
    raised ``TypeError`` on the first request while 44 tests reported success.

    That is practice #22 in its purest form: the test and the thing under test came from
    one author's single idea of the interface, so they agreed with each other and with
    nothing else. The fix is not a better assertion about the signature — it is to stop
    being the one who decides what the signature is, and let the real consumer decide.
    """

    def test_a_crudrouter_binds_them_and_serves_a_real_request(self, session: Session) -> None:
        """Construct the router the app constructs, and drive a request through it.

        Construction alone is not enough and neither is ``isinstance(policy, Policy)``:
        ``Policy`` is ``runtime_checkable``, and a runtime-checkable Protocol compares
        **method names only, never signatures**. The broken version had all four names, so
        both of those checks would have passed on it. Only an actual call through the
        router puts the arguments in the router's order rather than the test's.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from calton.api.v1.labels import build_crud_router
        from calton.core.errors import register_exception_handlers
        from calton.db.session import get_db

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(build_crud_router().router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: session

        @app.middleware("http")
        async def _subject(request, call_next):  # type: ignore[no-untyped-def]
            from types import SimpleNamespace

            request.state.auth = SimpleNamespace(id=ALICE)
            return await call_next(request)

        client = TestClient(app, raise_server_exceptions=False)

        # One request per method, because the four policy methods have separate
        # signatures and a mismatch on any one of them is invisible from the others.
        assert client.get("/api/v1/labels").status_code == 200
        assert client.get("/api/v1/labels/950").status_code == 200
        assert client.put("/api/v1/labels", json={"title": "bound"}).status_code == 201
        assert client.post("/api/v1/labels/950", json={"title": "renamed"}).status_code == 200
        assert client.delete("/api/v1/labels/950").status_code == 200

    def test_the_old_calling_convention_would_fail_this(self, session: Session) -> None:
        """The canary — proof the test above can go red.

        A policy shaped the way the pre-``step 0`` one was (no ``session`` parameter, a
        ``session_for`` callable instead) must break when the router calls it, since the
        router passes ``session`` positionally and ``auth`` by keyword. Without this, the
        test above is just "the current code works", and the next signature change to
        ``Policy`` would slip through exactly as the last one did.
        """
        from typing import Any

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from calton.core.crud_router import CRUDRouter
        from calton.core.errors import register_exception_handlers
        from calton.db.session import get_db
        from calton.schemas.label import LabelRead
        from calton.schemas.label import LabelWrite as Write

        class OldStylePolicy:
            """Every method name the Protocol wants, and the wrong signature on each."""

            def __init__(self, session_for: Any) -> None:
                self._session_for = session_for

            def can_read(self, auth: Any, **kwargs: Any) -> tuple[bool, int]:
                return True, 0

            def can_create(self, auth: Any, **kwargs: Any) -> bool:
                return True

            def can_update(self, auth: Any, **kwargs: Any) -> bool:
                return True

            def can_delete(self, auth: Any, **kwargs: Any) -> bool:
                return True

        # It satisfies the runtime-checkable Protocol, which is the point: that check
        # would have certified the broken version too.
        from calton.core.policy import Policy

        assert isinstance(OldStylePolicy(lambda: session), Policy)

        app = FastAPI()
        register_exception_handlers(app)
        crud: CRUDRouter[Any, LabelRead, Write] = CRUDRouter(
            prefix="/labels",
            item_param="label",
            service=LabelService(),
            policy=OldStylePolicy(lambda: session),  # type: ignore[arg-type]
            read_schema=LabelRead,
            write_schema=Write,
        )
        app.include_router(crud.router)
        app.dependency_overrides[get_db] = lambda: session

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/labels/950").status_code == 500
