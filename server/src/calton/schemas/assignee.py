"""Request and response bodies for the assignee endpoints.

Two of the three shapes here exist to preserve something that looks like an upstream
oversight, so none of them should be "tidied" without re-measuring:

* ``AssigneeCreated.created`` echoes the bound request struct, which upstream never fills
  in — so it is the zero time for every body that omits it, and the client's own value for
  one that sends it.
* ``BulkAssignees.assignees`` is genuinely nullable. ``{}`` in gives ``null`` back;
  ``{"assignees": []}`` gives ``[]``. Collapsing the two loses a distinction the wire
  format makes.
"""

from __future__ import annotations

from pydantic import ConfigDict

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserEcho


class AssigneeWrite(CaltonModel):
    """``PUT /tasks/{task}/assignees``.

    ``user_id`` is optional at this layer on purpose. A body with no ``user_id`` must
    reach the service as 0 and come back as the measured 404/1005; declaring it required
    would make FastAPI answer its own 422 before any of that runs. ``strict`` keeps a
    JSON string from being coerced into an int — measured, ``{"user_id": "901"}`` is
    400/2004 upstream, not a successful assignment.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    user_id: int = 0
    #: Read-only and echoed, never written. Upstream answers the struct it bound, so a body
    #: carrying `created` gets it back verbatim; a body without one gets the zero time.
    #: Measured both ways. Hardcoding the zero agrees with the second case only.
    created: Timestamp = ZERO_TIME


class AssigneeCreated(CaltonModel):
    """The ``201`` body of a single assign: the two fields upstream echoes, no more."""

    user_id: int
    created: Timestamp


class BulkAssigneeEntry(UserEcho):
    """One entry of a bulk *request*.

    Only ``id`` is ever *acted* on. Clients round-trip whole user objects back into this
    endpoint, so every other field has to be accepted rather than rejected — which is also
    why the request cannot reuse ``UserRead``: that one requires ``created`` and
    ``updated``, and a client sending ``[{"id": 902}]`` (the corpus's own shape) would get
    a 422 that upstream never produces.

    ⚠️ **The other fields are kept, not discarded.** This used to declare ``id`` alone with
    ``extra="ignore"``, so ``name``/``username``/``created`` were dropped at parse time and
    the response rebuilt each entry as ``id`` plus zeros. Measured: a body carrying
    ``{"id": 901, "username": "FORGED", "name": "Forged Name", "created": "1999-03-04…"}``
    echoes **all four back**, because upstream parses into the user struct and serialises
    that same struct. Dropping them agrees with the echo only for the id-only body — which
    is the corpus's shape and never the read-modify-write client's.
    """


class BulkAssigneesWrite(CaltonModel):
    """The bulk request body.

    ``None`` and ``[]`` are different values, not two spellings of "empty": ``{}`` echoes
    ``null`` back and ``{"assignees": []}`` echoes ``[]``. Both clear the set.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    assignees: list[BulkAssigneeEntry] | None = None


class BulkAssignees(CaltonModel):
    """The bulk response: the request's user objects, echoed verbatim and unhydrated.

    ``UserEcho`` rather than ``UserRead`` because these values came out of a request body,
    where the timestamps are optional. Validating the echo against the read model would
    raise **after the assignment has already committed** — the rows are written and the
    client gets a 500, so a retry does the work twice.
    """

    assignees: list[UserEcho] | None = None
