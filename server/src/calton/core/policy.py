"""Permission checks, mirroring web.Permissions (pkg/web/web.go:25-30).

Denials do not go through the generated error table. pkg/web/handler/error.go
defines its own ``ErrGenericForbidden`` whose ``HTTPError()`` leaves ``Code``
unset, so the body is ``{"code": 0, "message": "Forbidden"}`` — *not* the
``{"code": 1, "message": "You're not allowed to do this."}`` that
``models.ErrGenericForbidden`` produces. Both exist; the CRUD pipeline uses the
former. ReadOne additionally overrides the wording.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from calton.core.errors import CaltonError

FORBIDDEN_MESSAGE = "Forbidden"
FORBIDDEN_READ_MESSAGE = "You don't have the permission to see this"

# Permission levels. -1 means no access at all, and is distinct from Read=0.
NO_PERMISSION = -1
READ = 0
WRITE = 1
ADMIN = 2


class ForbiddenError(CaltonError):
    """403 with code 0, as the CRUD pipeline emits it (handler/core.go:49)."""

    def __init__(self, message: str = FORBIDDEN_MESSAGE) -> None:
        super().__init__(code=0, message=message, http_status=403)


@runtime_checkable
class Policy(Protocol):
    """What a resource must answer before the router will touch it.

    ``can_read`` returns the caller's maximum permission alongside the verdict;
    the router puts it in ``x-max-permission``.

    Every method takes the request's ``session`` rather than opening its own. The Policy
    and the Service must share one session per request, or a permission check runs in a
    different transaction from the write it guards and cannot see it. That failure is
    invisible in unit tests — each layer works fine alone — and shows up in production as
    "the object I just created says I have no access to it".
    """

    def can_read(self, session: Session, auth: Any, **kwargs: Any) -> tuple[bool, int]: ...

    def can_create(self, session: Session, auth: Any, **kwargs: Any) -> bool: ...

    def can_update(self, session: Session, auth: Any, **kwargs: Any) -> bool: ...

    def can_delete(self, session: Session, auth: Any, **kwargs: Any) -> bool: ...


class AllowAll:
    """A Policy that permits everything. For tests and for genuinely open routes."""

    def can_read(self, session: Session, auth: Any, **kwargs: Any) -> tuple[bool, int]:
        return True, ADMIN

    def can_create(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        return True

    def can_update(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        return True

    def can_delete(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        return True
