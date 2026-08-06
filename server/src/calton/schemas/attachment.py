"""Task attachment response bodies (T32).

The wire shape is narrower than the table: ``task_attachments`` has ``file_id`` and
``created_by_id`` columns, and **neither appears on the wire** — they are ``json:"-"``,
replaced by the hydrated ``file`` and ``created_by`` objects. Emitting the raw ids as
well is the natural mistake and adds two fields upstream never sends.

Both hydrated fields are genuinely nullable, and the seed exercises both cases:

* ``file`` is ``null`` when the ``files`` row is missing (fixture attachment 2 points at
  file 9999, which does not exist). Upstream's ReadAll skips hydration for those rather
  than failing the whole list.
* ``created_by`` is ``null`` when the uploader is not a real user (fixture attachment 3
  has ``created_by_id`` -2, a link share). Upstream swallows the lookup error so the
  user-deletion cascade can still run.

A list request that hits either of these must still answer 200 with the other entries
intact — measured on ``GET /tasks/1/attachments``, which returns all three.
"""

from __future__ import annotations

from pydantic import ConfigDict

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class FileRead(CaltonModel):
    """``pkg/files.File`` as it appears nested in an attachment.

    Five fields only. The table also has ``created_by_id``, which is ``json:"-"``, and
    the bytes are never inlined — they come from the download endpoint.
    """

    id: int
    name: str
    mime: str = ""
    size: int
    created: Timestamp


class AttachmentRead(CaltonModel):
    id: int
    task_id: int
    created_by: UserRead | None = None
    file: FileRead | None = None
    created: Timestamp


class FileEcho(CaltonModel):
    """``FileRead`` with every field optional, for the *request* side.

    Same keys, no requirements: a write body carries whatever the client read back, and
    a required field here would 422 a body upstream accepts.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    id: int = 0
    name: str = ""
    mime: str = ""
    size: int = 0
    created: Timestamp = ZERO_TIME


class AttachmentEcho(CaltonModel):
    """An attachment inside a **write** body — parsed only so it can be echoed back.

    Nothing here is ever written: measured on the reference server, posting a fabricated
    entry to ``POST /tasks/{id}`` or ``POST /tasks/bulk`` creates no row, and posting
    ``[]`` deletes none. Attachments change only through the multipart upload endpoint.

    It is parsed rather than passed through as a raw dict because the echo is **the
    parsed struct re-serialised**, not the bytes the client sent. Measured: a client
    sending ``{"id": 999, "task_id": 950}`` gets back all five keys —
    ``{"id": 999, "task_id": 950, "created_by": null, "file": null,
    "created": "0001-01-01T00:00:00Z"}``. Echoing the raw dict returns two keys and
    diverges on every read-modify-write update.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    id: int = 0
    task_id: int = 0
    created_by: UserRead | None = None
    file: FileEcho | None = None
    created: Timestamp = ZERO_TIME


class AttachmentUploadError(CaltonModel):
    """One per-file failure inside an otherwise successful upload.

    ``code`` is the domain error's numeric code when it has one (4012 for a file over the
    size limit) and **0** otherwise, because upstream builds this from
    ``web.HTTPErrorProcessor`` and falls back to a bare ``err.Error()`` string with the
    code left at its zero value.
    """

    code: int = 0
    message: str = ""


class AttachmentUploadResult(CaltonModel):
    """The 200 body of an upload — note the status: **200, not 201**, unlike every other
    create in v1. This endpoint is a hand-written handler rather than ``CreateWeb``.

    ``errors`` and ``success`` are both ``null`` rather than ``[]`` when empty, which is
    what a nil Go slice serialises to. Measured: an upload whose form field is not called
    ``files`` answers ``{"errors": null, "success": null}`` with a 200 — it is not an
    error, it simply uploaded nothing.

    Per-file failures do **not** fail the request. A batch of one small file and one
    oversized file answers 200 with the small one in ``success`` and the large one in
    ``errors``, and the small one really is persisted. This is the opposite of the bulk
    task endpoint's all-or-nothing rule; the two were measured separately for that reason.
    """

    errors: list[AttachmentUploadError] | None = None
    success: list[AttachmentRead] | None = None
