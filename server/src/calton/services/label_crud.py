"""The label resource, wired for :class:`~calton.core.crud_router.CRUDRouter`.

The pipeline is: policy first, service second. The policy's refusal becomes 403 and only
the service can raise anything else, which forces one counterintuitive decision here.

**``can_update`` and ``can_delete`` answer True for a label that does not exist.** That
reads like a hole and is the only way to reproduce upstream: a missing label must answer
404/8002 on the write paths, and if the policy refused it the pipeline would answer 403
before the service ever ran. So the policy passes the missing case through and
:func:`~calton.services.label_service.load_for_write` raises the 404. Someone else's
*existing* label is still refused by the policy and is still a 403.

The read path needs no such trick: missing and invisible are both 403 there, which is
what the policy naturally produces.

**Every method takes the request's ``session`` as its first argument**, because that is
what :class:`~calton.core.policy.Policy` and
:class:`~calton.core.crud_router.CrudService` declare. An earlier version of this module
took a ``session_for`` callable in the constructor instead; it satisfied its own unit
tests and could not be handed to ``CRUDRouter`` at all — the router passes ``session``
positionally and ``auth`` by keyword, so binding one raised ``TypeError`` on the first
request. Nothing caught it because no ``CRUDRouter`` was ever constructed from it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from calton.core.errors import UnauthorizedError
from calton.db.base import utcnow
from calton.models import Label
from calton.schemas.label import LabelWrite
from calton.services.label_service import (
    can_modify_label,
    can_read_label,
    load_for_read,
    load_for_write,
    visible_labels_query,
)

#: ``x-max-permission`` on a single label read. Upstream's ``PermissionRead``/
#: ``PermissionAdmin``; there is no Write in between for labels, because the only two
#: outcomes are "the creator" and "someone who can see it".
LABEL_READ = 0
LABEL_ADMIN = 2


def _user_id(auth: Any) -> int:
    """The authenticated subject's id, or the 401 the middleware would have sent.

    ``CRUDRouter`` runs the policy before anything else and has no authentication step of
    its own — it passes ``request.state.auth`` straight through, which is ``None`` until
    the JWT (T14) or API token (T15) middleware has populated it. The obvious spelling,
    ``int(getattr(auth, "id", auth))``, then raises ``TypeError`` on ``int(None)`` and the
    endpoint answers **500** to every anonymous request. Schemathesis found it on all six
    label routes.

    Refusing here rather than defaulting to some user id is the same rule ``api/v1/tasks``
    states for its own handlers: a missing subject is 401, never a silent fallback that
    would make the resource publicly writable.
    """
    user_id = getattr(auth, "id", auth)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


class LabelPolicy:
    """Answers the four questions CRUDRouter asks before touching the resource."""

    def can_read(self, session: Session, auth: Any, **kwargs: Any) -> tuple[bool, int]:
        """Missing and invisible are both refused, and indistinguishably so.

        Reporting 404 for the missing case would let a caller enumerate label ids.

        The second value becomes ``x-max-permission``, and it is **not** a constant:
        ``hasAccessToLabel`` (``label_permissions.go:132-137``) reports Admin (2) to the
        label's creator and Read (0) to everyone else who can see it, because updating and
        deleting a label are owner-only whatever route reached it.

        ⚠️ This returned a bare 0 before, and the tempting repair — make it 2, since the
        one label the corpus reads comes back as 2 — is wrong in half the matrix. Every
        label the corpus touches is owned by the caller, so "always Admin" and the real
        rule agree on all of it. Measured across both users and all six seeded labels
        (``harness/probe_coder_e_label_perm.py``), the two rules disagree in three cells:
        label 954 is bob's and visible to alice, who gets 0 while bob gets 2, and labels
        950/951 are alice's and visible to bob the same way round.
        """
        user_id = _user_id(auth)
        label_id = int(kwargs.get("label", 0))
        if not can_read_label(session, user_id, label_id):
            return False, 0
        # Ownership, not write access to some project — `can_modify_label` is the same
        # `created_by_id` test `isLabelOwner` makes.
        return True, LABEL_ADMIN if can_modify_label(session, user_id, label_id) else LABEL_READ

    def can_create(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """Any *authenticated* user may create a label, with no validation of the body."""
        _user_id(auth)
        return True

    def can_update(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        """True for a missing label — see the module docstring. Not a hole.

        The subject is resolved **before** the label lookup, so an anonymous request never
        reaches the database. Doing it the other way round is not merely untidy: the
        existence query runs on behalf of nobody, and on a database where the query itself
        fails the endpoint answers 500 rather than 401. That is how schemathesis first
        reported this.
        """
        user_id = _user_id(auth)
        label_id = int(kwargs.get("label", 0))
        if session.get(Label, label_id) is None:
            return True
        return can_modify_label(session, user_id, label_id)

    def can_delete(self, session: Session, auth: Any, **kwargs: Any) -> bool:
        return self.can_update(session, auth, **kwargs)


class LabelService:
    """The five operations, each assuming its policy has already run."""

    def create(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Label:
        """No validation: an empty title and an empty body are both accepted."""
        body = data if isinstance(data, LabelWrite) else LabelWrite.model_validate(data)

        label = Label(
            title=body.title,
            description=body.description,
            hex_color=body.hex_color,
            created_by_id=_user_id(auth),
            created=utcnow(),
            updated=utcnow(),
        )
        session.add(label)
        session.flush()
        return label

    def read_one(self, session: Session, auth: Any, **kwargs: Any) -> Label:
        return load_for_read(session, _user_id(auth), int(kwargs.get("label", 0)))

    def read_all(
        self,
        session: Session,
        auth: Any,
        search: str = "",
        page: int = 1,
        per_page: int = 0,
        **kwargs: Any,
    ) -> tuple[list[Label], int, int]:
        """Own labels plus any on a visible task. No permission gate above this.

        ``search`` is accepted and ignored for now: upstream reads it as a list of label
        **ids** when every comma-separated part parses as a number and as a title ILIKE
        otherwise, which is its own piece of work. Declaring the parameter rather than
        swallowing it into ``**kwargs`` keeps that visible.
        """
        labels = list(session.scalars(visible_labels_query(session, _user_id(auth))))
        return labels, len(labels), len(labels)

    def update(self, session: Session, data: BaseModel, auth: Any, **kwargs: Any) -> Label:
        """Whole-model replacement: a field the body omits is reset, not preserved."""
        label = load_for_write(session, _user_id(auth), int(kwargs.get("label", 0)))
        body = data if isinstance(data, LabelWrite) else LabelWrite.model_validate(data)

        label.title = body.title
        label.description = body.description
        label.hex_color = body.hex_color
        label.updated = utcnow()
        session.flush()
        return label

    def delete(self, session: Session, auth: Any, **kwargs: Any) -> None:
        label = load_for_write(session, _user_id(auth), int(kwargs.get("label", 0)))
        session.delete(label)
        session.flush()
