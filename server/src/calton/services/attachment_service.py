"""Task attachments: list, upload, download, delete (T32).

Measured against the running Go reference server. Four things here are not what the
neighbouring endpoints do, and each was measured rather than assumed:

**1. The forbidden body is code 1, not code 0.** These handlers return
``models.ErrGenericForbidden`` directly, so a denial is
``{"code": 1, "message": "You're not allowed to do this."}``. The CRUD pipeline's
denial — what tasks, projects and labels answer — is
``{"code": 0, "message": "Forbidden"}``. Both exist upstream and they are not
interchangeable; the bulk endpoint in ``task_bulk`` really does answer code 0 while these
answer code 1, and that pair was measured in the same session.

**2. Read access to the *task* is the only gate.** There is no separate attachment
permission: anyone who can read the task can download every attachment on it, and anyone
who can write the task can upload and delete. Delete does not check who uploaded it.

**3. A wrong ``{task}`` for a real attachment is 404, not 403.** ``ReadOne`` adds
``task_id = ?`` to the lookup, so attachment 5 requested through task 925 answers
404/4011 exactly like a non-existent id. That ``AND`` is the IDOR guard; dropping it
makes every attachment reachable through any task the caller can read.

**4. Upload is per-file, not all-or-nothing.** See ``AttachmentUploadResult``.

Gate order on upload, measured: the task must exist (404/4002) before the caller's write
permission is checked (403/1), and both precede anything to do with the files themselves.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.core.pseudo_users import resolve_subject
from calton.db.base import utcnow
from calton.db.types import ZERO_TIME
from calton.models import base_task_query
from calton.models.file import File
from calton.models.task import Task
from calton.models.task_comment import TaskAttachment
from calton.permissions import task as task_permissions
from calton.schemas.attachment import (
    AttachmentRead,
    AttachmentUploadError,
    AttachmentUploadResult,
    FileRead,
)
from calton.schemas.user import UserRead
from calton.services import file_storage
from calton.services.file_storage import FileStorage


def _forbidden() -> CaltonError:
    """403/1 — ``models.ErrGenericForbidden``, **not** the CRUD pipeline's 403/0."""
    return CaltonError.from_name("models.ErrGenericForbidden")


def _attachment_not_found() -> CaltonError:
    return CaltonError.from_name("models.ErrTaskAttachmentDoesNotExist")


def missing_bytes_error() -> CaltonError:
    """The registered deviation: 404/4011 where upstream answers **500**.

    Upstream's download opens the stored file and lets the failure escape, so an
    attachment row whose bytes are gone answers ``500 {"message": "Internal Server
    Error"}``. Measured on the seed's fixture attachment 1 — this is not a hypothetical
    path, the parity corpus reaches it.

    Not reproduced, on the rule that upstream's *bugs* are copied but its *loss of
    control* is not: shipping a known 500 is worse than a controlled divergence. 4011 is
    chosen over inventing a code because the observable truth is the same one the client
    already handles — this attachment cannot be served. Registered in
    ``harness/corpus/_deviations.yaml`` with both sides pinned, so neither can drift
    silently and nobody "aligns" it back to a 500.
    """
    return _attachment_not_found()


def _require_task(session: Session, task_id: int) -> Task:
    """The task, or 404/4002. Runs before any permission check — measured: an upload to a
    task that does not exist is 404, even for a caller who could not have written it."""
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    return task


def _user_view(session: Session, user_id: int) -> UserRead | None:
    """The uploader as it appears on the wire, or ``None`` when neither a user nor a
    link share backs the id. Link shares (negative ids) render as pseudo-users —
    upstream's ``getUsersOrLinkSharesFromIDs`` does this for every ``created_by`` field,
    so this routes through the shared resolver rather than reaching for ``User`` directly.
    """
    return resolve_subject(session, user_id)


def _file_view(stored: File) -> FileRead:
    """One ``files`` row on the wire.

    ⚠️ Built field by field rather than with ``model_validate(..., from_attributes=True)``
    because **``files.mime`` and ``files.created`` are nullable in the schema while the Go
    struct fields are not**: upstream writes zero values, but the upstream *fixtures* do
    not, and the seed ships a row with a NULL mime (file 1, the one three fixture
    attachments point at). ``from_attributes`` passes that NULL straight into a ``str``
    field and the whole list 500s — which no test using only freshly uploaded files can
    reach, because every upload writes a real mime. Found by replaying the seed.
    """
    return FileRead(
        id=stored.id,
        name=stored.name,
        mime=stored.mime or "",
        size=stored.size,
        created=stored.created if stored.created is not None else ZERO_TIME,
    )


def _view(session: Session, attachment: TaskAttachment, files: dict[int, File]) -> AttachmentRead:
    stored = files.get(attachment.file_id)
    return AttachmentRead(
        id=attachment.id,
        task_id=attachment.task_id,
        created_by=_user_view(session, attachment.created_by_id),
        # Left null rather than fabricated when the files row is gone; see the schema.
        file=None if stored is None else _file_view(stored),
        created=attachment.created if attachment.created is not None else ZERO_TIME,
    )


def _files_for(session: Session, attachments: list[TaskAttachment]) -> dict[int, File]:
    if not attachments:
        return {}
    ids = {a.file_id for a in attachments}
    return {f.id: f for f in session.scalars(select(File).where(File.id.in_(ids))).all()}


def list_attachments(session: Session, *, task_id: int, user_id: int) -> list[AttachmentRead]:
    """``GET /tasks/{task}/attachments``. The caller needs read on the task."""
    _require_task(session, task_id)
    can_read, _ = task_permissions.can_read(session, user_id, task_id)
    if not can_read:
        raise _forbidden()

    rows = list(
        session.scalars(
            select(TaskAttachment)
            .where(TaskAttachment.task_id == task_id)
            .order_by(TaskAttachment.id)
        ).all()
    )
    files = _files_for(session, rows)
    return [_view(session, row, files) for row in rows]


def get_attachment(session: Session, *, task_id: int, attachment_id: int) -> TaskAttachment:
    """One attachment, scoped to its task.

    The ``task_id`` clause is the IDOR guard from point 3 of the module docstring: it is
    what makes a real attachment requested through the wrong task answer 404/4011 rather
    than serving the bytes.
    """
    row = session.scalars(
        select(TaskAttachment).where(
            TaskAttachment.id == attachment_id, TaskAttachment.task_id == task_id
        )
    ).one_or_none()
    if row is None:
        raise _attachment_not_found()
    return row


def load_for_download(
    session: Session, *, task_id: int, attachment_id: int, user_id: int
) -> tuple[TaskAttachment, File | None]:
    """The attachment and its ``files`` row, after checking read access to the task."""
    _require_task(session, task_id)
    can_read, _ = task_permissions.can_read(session, user_id, task_id)
    if not can_read:
        raise _forbidden()

    attachment = get_attachment(session, task_id=task_id, attachment_id=attachment_id)
    return attachment, session.get(File, attachment.file_id)


def upload(
    session: Session,
    *,
    task_id: int,
    user_id: int,
    uploads: list[tuple[str, bytes]],
    storage: FileStorage,
) -> AttachmentUploadResult:
    """``PUT /tasks/{task}/attachments``. ``uploads`` is ``[(filename, content), ...]``.

    Each file is independent: one that is refused lands in ``errors`` and the rest are
    still stored and still committed. Only a failure of the request as a whole — the task
    missing, or the caller not allowed to write it — raises.
    """
    _require_task(session, task_id)
    if not task_permissions.can_write(session, user_id, task_id):
        raise _forbidden()

    created: list[AttachmentRead] = []
    failures: list[AttachmentUploadError] = []

    for filename, content in uploads:
        # The limit applies to the bytes received, never to a size the client declares —
        # upstream measures the reader for exactly this reason (GHSA-qh78-rvg3-cv54).
        if len(content) > file_storage.MAX_SIZE_BYTES:
            spec = CaltonError.from_name(
                "models.ErrTaskAttachmentIsTooLarge",
                files_max_size=file_storage.configured_size_for_message(),
                size=len(content),
            )
            failures.append(AttachmentUploadError(code=spec.code, message=spec.message))
            continue

        now: datetime = utcnow()
        stored = File(
            name=filename,
            mime=file_storage.detect_mime(content),
            size=len(content),
            created=now,
            created_by_id=user_id,
        )
        session.add(stored)
        # Needed before the bytes can be written: the storage path *is* the row's id.
        session.flush()

        attachment = TaskAttachment(
            task_id=task_id, file_id=stored.id, created_by_id=user_id, created=now
        )
        session.add(attachment)
        session.flush()

        storage.save(stored.id, content)
        created.append(_view(session, attachment, {stored.id: stored}))

    session.commit()
    # null, not [], when nothing landed in either bucket — a nil Go slice.
    return AttachmentUploadResult(
        errors=failures or None,
        success=created or None,
    )


def delete_attachment(
    session: Session, *, task_id: int, attachment_id: int, user_id: int, storage: FileStorage
) -> None:
    """``DELETE /tasks/{task}/attachments/{attachment}``.

    ⚠ The 403 body here is **``{"code": 0, "message": "Forbidden"}``**, *not* the
    `{code: 1, ...}` ``models.ErrGenericForbidden`` that the GET path returns on the
    same task. The DELETE path goes through ``web/handler/core.go::DoDelete``'s
    permission check, which raises the ``web/handler`` package's ``ErrGenericForbidden``
    — a separate type whose ``HTTPError()`` leaves ``Code`` at its zero value. The GET
    path's denial bubbles out of ``models.ReadAll`` and uses ``models.ErrGenericForbidden``.
    Two real body shapes for "no access", distinguished by the verb, measured and pinned
    in the corpus: ``attachment.delete.forbidden_is_code_0_not_code_1`` and
    ``attachment.read_one.unreadable_task_is_403_not_404`` respectively.
    """
    _require_task(session, task_id)
    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError()

    attachment = get_attachment(session, task_id=task_id, attachment_id=attachment_id)
    file_id = attachment.file_id
    stored = session.get(File, file_id)

    session.delete(attachment)
    if stored is not None:
        session.delete(stored)
    session.commit()
    storage.delete(file_id)
