"""The four task-attachment endpoints (T32).

Two of these are hand-written handlers upstream rather than ``WebHandler`` methods, and
that shows on the wire in ways the other resources do not prepare you for:

* **Upload is ``PUT`` and answers 200, not 201.** Every other v1 create answers 201.
* **Their bind failures have no ``code``.** ``GET /tasks/abc/attachments/1`` answers
  400 ``{"message": "No task ID provided"}`` — a bare string error, not the 400/2004
  ``{"code": 2004, ...}`` that the ``WebHandler`` routes produce for the same bad path.
  The list and delete routes *do* go through ``WebHandler``, so on those two the very
  same malformed segment is 400/2004. Measured, all four:

      GET    /tasks/abc/attachments      -> {"code": 2004, "message": "Invalid model ..."}
      DELETE /tasks/924/attachments/abc  -> {"code": 2004, "message": "Invalid model ..."}
      GET    /tasks/abc/attachments/1    -> {"message": "No task ID provided"}
      GET    /tasks/924/attachments/abc  -> {"message": "No task ID provided"}

  This is why the path parameters are parsed by hand per route instead of being declared
  as ``int``: FastAPI's own 422 is wrong for all four, and the right answer differs
  between them.

The download response is compared header-by-header by the parity harness, so every header
below was read off the reference server rather than chosen — including ``Accept-Ranges``,
the 206 for a ``Range`` request and the 304 for ``If-Modified-Since``, all of which
upstream gets for free from ``http.ServeContent`` and we have to produce deliberately.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from calton.config import get_settings
from calton.core.crud_router import deleted_response, path_param_as_id
from calton.core.errors import EchoStringError
from calton.core.pagination import Paginator
from calton.db.session import get_db
from calton.schemas.attachment import AttachmentRead, AttachmentUploadResult
from calton.schemas.message import Message
from calton.services import attachment_service
from calton.services.file_storage import FileStorage

#: Both produced by ``echo.NewHTTPError`` in the two hand-written handlers, so they render
#: as ``{"message": ...}`` with **no code** field. Adding a code would fork the contract
#: for any client that branches on its presence.
NO_TASK_ID = "No task ID provided"
NO_MULTIPART = "No multipart form provided"

#: The multipart field name. A form that uses any other name uploads nothing and still
#: answers 200 — measured, ``{"errors": null, "success": null}``.
UPLOAD_FIELD = "files"

#: Request-scope key this module sets to opt out of the API-wide ``Cache-Control:
#: no-store`` **entirely** (as opposed to overriding it). Read by ``main``'s cache-control
#: middleware. Defined here rather than in ``main`` because ``main`` imports this module,
#: and the reverse direction would be an import cycle. Its only user is the 416 below,
#: which upstream sends with no Cache-Control header at all.
SUPPRESS_CACHE_CONTROL = "calton.suppress_cache_control"

#: RFC 2045 token characters. ``mime.FormatMediaType`` leaves a filename unquoted when it
#: is entirely made of these, quotes it when it is not, and switches to RFC 5987
#: ``filename*=`` when it is not ASCII. Measured on all three shapes.
_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'*+-.^_`|~"
)


def content_disposition(filename: str) -> str:
    """``mime.FormatMediaType("attachment", {"filename": name})``, measured three ways.

        "plain.txt"       -> attachment; filename=plain.txt
        "with space.txt"  -> attachment; filename="with space.txt"
        "unicode-中文.txt" -> attachment; filename*=utf-8''unicode-%E4%B8%AD%E6%96%87.txt

    The quoted form is not interchangeable with the bare one: the harness compares this
    header byte for byte.
    """
    if not filename.isascii():
        quoted = urllib.parse.quote(filename, safe="")
        return f"attachment; filename*=utf-8''{quoted}"
    if filename and all(character in _TOKEN_CHARS for character in filename):
        return f"attachment; filename={filename}"
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'attachment; filename="{escaped}"'


def _http_date(value: datetime) -> str:
    """``http.TimeFormat`` — always GMT, always the same fixed-width shape."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _parse_http_date(raw: str) -> datetime | None:
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


#: The two bodies ``http.ServeContent`` writes for a Range it will not serve. Both end in
#: a newline, both are the whole body, and they are **different errors**: the first means
#: the header could not be parsed, the second that it parsed but named nothing that
#: exists. Only the second carries ``Content-Range: bytes */<size>``. Measured.
RANGE_UNPARSEABLE = b"invalid range\n"
RANGE_NO_OVERLAP = b"invalid range: failed to overlap\n"


class RangeNotSatisfiableError(Exception):
    """A 416. ``content_range`` is set only for the overlap failure, not the parse one."""

    def __init__(self, body: bytes, content_range: str | None) -> None:
        self.body = body
        self.content_range = content_range


def parse_range(header: str, size: int) -> list[tuple[int, int]]:
    """``http.ParseRange``. Returns the spans, or raises ``RangeNotSatisfiableError``.

    An empty list means "no Range header worth acting on" and the caller sends the full
    200. Anything the parser rejects is a **416**, not a fallback to 200 — measured, and
    the distinction is easy to get backwards because ignoring a bad header feels lenient:

        Range: bytes=0-3          -> 206 bytes 0-3/22
        Range: bytes=-5           -> 206 bytes 17-21/22   (suffix: the last five)
        Range: bytes=3-           -> 206 bytes 3-21/22    (open ended)
        Range: bytes=999-1000     -> 416 + Content-Range: bytes */22
        Range: bytes=22-          -> 416 (start == size is already past the end)
        Range: kilometres=0-3     -> 416, no Content-Range
        Range: bytes=abc          -> 416, no Content-Range
    """
    if not header:
        return []
    if not header.startswith("bytes="):
        raise RangeNotSatisfiableError(RANGE_UNPARSEABLE, None)

    spans: list[tuple[int, int]] = []
    for piece in header[len("bytes=") :].split(","):
        spec = piece.strip()
        if not spec:
            raise RangeNotSatisfiableError(RANGE_UNPARSEABLE, None)
        start_raw, sep, end_raw = spec.partition("-")
        if not sep:
            raise RangeNotSatisfiableError(RANGE_UNPARSEABLE, None)
        try:
            if not start_raw:
                length = int(end_raw)
                if length <= 0:
                    raise RangeNotSatisfiableError(RANGE_UNPARSEABLE, None)
                start, end = max(0, size - length), size - 1
            else:
                start = int(start_raw)
                end = int(end_raw) if end_raw else size - 1
                end = min(end, size - 1)
        except ValueError as exc:
            raise RangeNotSatisfiableError(RANGE_UNPARSEABLE, None) from exc
        if start < 0 or start >= size or start > end:
            # Parsed fine, names nothing that exists — the other 416, with Content-Range.
            raise RangeNotSatisfiableError(RANGE_NO_OVERLAP, f"bytes */{size}")
        spans.append((start, end))
    return spans


def _binds_as_json(body: bytes, content_type: str) -> bool:
    """Whether ``c.Bind`` would have succeeded on this body.

    Echo only decodes the body when the Content-Type says JSON; for anything else it
    binds path and query parameters alone and cannot fail here. An empty body is also
    fine — Bind skips it.
    """
    if "application/json" not in content_type:
        return True
    if not body.strip():
        return True
    import json

    try:
        json.loads(body)
    except ValueError:
        return False
    return True


def _multipart_is_complete(body: bytes, content_type: str) -> bool:
    """Whether a multipart body carries its closing ``--<boundary>--`` delimiter.

    Returns True when no boundary is declared in the header — that case is already
    rejected further down by the parser itself, and duplicating the decision here would
    give the same input two different error paths.

    An **empty** body fails this check. Upstream answers 500 for that one (measured), so
    the two disagree, but a controlled 400 that matches the truncated case is better than
    either reproducing a 5xx or answering 200 to a body that carried nothing. Registered
    in ``harness/corpus/_deviations.yaml``.
    """
    marker = "boundary="
    if marker not in content_type:
        return True
    boundary = content_type.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        return True
    return f"--{boundary}--".encode() in body


async def _uploads_from(request: Request) -> list[tuple[str, bytes]]:
    """The multipart ``files`` parts, as ``(filename, bytes)``.

    An ``async`` **dependency** rather than an ``async`` handler on purpose: reading the
    body has to be awaited, but the handler below stays a plain ``def`` so FastAPI runs
    it — and all of its synchronous database work — in the threadpool, which is the rule
    ``db.session`` sets out. Declaring the files as ``UploadFile`` parameters instead
    would hand the error path to FastAPI, which answers 422; this API never does.
    """
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" not in content_type:
        # ⚠️ Two different errors, and the order is the opposite of the intuitive one.
        # Upstream binds the request *first* (`c.Bind`, which parses the body according to
        # its declared Content-Type) and only then asks for the multipart form. So:
        #
        #   Content-Type: application/json  + valid JSON  -> binds, then NO_MULTIPART
        #   Content-Type: application/json  + a multipart -> bind fails -> NO_TASK_ID
        #
        # Both measured. Checking "is it multipart?" first answers NO_MULTIPART to both,
        # which looks more sensible and is wrong for the second.
        if _binds_as_json(await request.body(), content_type):
            raise EchoStringError(400, NO_MULTIPART)
        raise EchoStringError(400, NO_TASK_ID)

    # ⚠️ Starlette's multipart parser is lenient where Go's is not: a truncated body
    # yields an *empty form* rather than raising, so without this check a mangled upload
    # answers 200 {"errors": null, "success": null} while upstream answers 400. Measured
    # both ways. The closing delimiter is the cheapest thing that distinguishes "the whole
    # body arrived" from "the connection died mid-upload", and it is exactly the case a
    # large attachment upload is most likely to hit.
    if not _multipart_is_complete(await request.body(), content_type):
        raise EchoStringError(400, NO_TASK_ID)

    try:
        form = await request.form()
    except Exception as exc:  # malformed multipart never reaches the model layer
        raise EchoStringError(400, NO_TASK_ID) from exc

    try:
        uploads: list[tuple[str, bytes]] = []
        for part in form.getlist(UPLOAD_FIELD):
            if isinstance(part, str):
                continue
            # Go's ``mime/multipart`` populates ``Form.File`` only with parts that
            # carry a non-empty filename; parts with ``filename=""`` are dropped from
            # the file collection (they land in ``Form.Value`` instead). Measured as a
            # silent 200 ``{"success": null}``: the empty-filename upload neither
            # creates a row nor fails — the request completes with nothing to store.
            # Without this drop Calton writes the row, which both gets this case wrong
            # and *raises the auto-increment id* for every later upload in the same
            # session — so the cascade to ``empty_file`` and ``two_files`` (id +1 each)
            # falls out of the same fix, not a separate one. See the corpus entries
            # tagged ``silent-noop``.
            name = part.filename or ""
            if name == "":
                continue
            uploads.append((name, await part.read()))
        return uploads
    finally:
        # Each part is a SpooledTemporaryFile; anything over Starlette's threshold is a
        # real file on disk. Not closing them leaks a descriptor per upload and surfaces
        # as a ResourceWarning at garbage-collection time — which lands on whichever
        # test happens to trigger the collection, not on the request that leaked.
        await form.close()


def build_router() -> APIRouter:
    router = APIRouter()

    def _storage() -> FileStorage:
        return FileStorage(get_settings().files.basepath)

    @router.get("/tasks/{task}/attachments", response_model=list[AttachmentRead])
    def read_attachments(
        request: Request,
        task: str,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        task_id = path_param_as_id(task)
        attachments = attachment_service.list_attachments(
            session, task_id=task_id, user_id=_user_id(request)
        )
        page = paginator.slice(attachments)
        # Through Paginator.response so the two pagination headers and their CORS
        # exposure travel with it — measured present here, and MCP clients loop until
        # they have seen x-pagination-total-pages pages.
        return paginator.response(
            [entry.model_dump(mode="json") for entry in page],
            total_items=len(attachments),
            result_count=len(page),
        )

    @router.put("/tasks/{task}/attachments", response_model=AttachmentUploadResult)
    def upload_attachments(
        request: Request,
        task: str,
        uploads: list[tuple[str, bytes]] = Depends(_uploads_from),
        session: Session = Depends(get_db),
    ) -> Any:
        # 200, not 201 — this handler is not CreateWeb. See the schema's docstring.
        task_id = _task_id_or_bare_error(task)
        return attachment_service.upload(
            session,
            task_id=task_id,
            user_id=_user_id(request),
            uploads=uploads,
            storage=_storage(),
        )

    @router.get("/tasks/{task}/attachments/{attachment}")
    def download_attachment(
        request: Request, task: str, attachment: str, session: Session = Depends(get_db)
    ) -> Response:
        task_id = _task_id_or_bare_error(task)
        attachment_id = _task_id_or_bare_error(attachment)

        _row, stored = attachment_service.load_for_download(
            session, task_id=task_id, attachment_id=attachment_id, user_id=_user_id(request)
        )
        content = None if stored is None else _storage().load(stored.id)
        if stored is None or content is None:
            # ⚠️ Upstream answers **500** here — measured on the seed's fixture
            # attachment 1, whose files row has never had bytes on disk. Reproducing a
            # 500 would mean shipping a known server error, so this is a registered
            # deviation (corpus `_deviations.yaml`) answering the attachment's own 404
            # instead. Both sides are pinned there; do not "align" it by returning 500.
            raise attachment_service.missing_bytes_error()

        return _file_response(request, stored.name, stored.mime or "", content, stored.created)

    @router.delete("/tasks/{task}/attachments/{attachment}", response_model=Message)
    def delete_attachment(
        request: Request, task: str, attachment: str, session: Session = Depends(get_db)
    ) -> Response:
        # These two go through WebHandler upstream, so a bad segment here is 400/2004 —
        # not the bare-string error the download handler gives for the same input.
        task_id = path_param_as_id(task)
        attachment_id = path_param_as_id(attachment)
        attachment_service.delete_attachment(
            session,
            task_id=task_id,
            attachment_id=attachment_id,
            user_id=_user_id(request),
            storage=_storage(),
        )
        return deleted_response()

    return router


def _user_id(request: Request) -> int:
    from calton.auth.deps import auth_user_id

    return auth_user_id(request)


def _task_id_or_bare_error(raw: str) -> int:
    """A path segment for the two hand-written handlers, where a bad one is NO_TASK_ID.

    ``path_param_as_id`` raises the 400/2004 the ``WebHandler`` routes use; these two
    routes answer a bare ``{"message": "No task ID provided"}`` instead, for both the
    task segment and the attachment segment. Measured — the difference is the handler,
    not the parameter.
    """
    try:
        return path_param_as_id(raw)
    except Exception as exc:
        raise EchoStringError(400, NO_TASK_ID) from exc


def _file_response(
    request: Request, name: str, mime: str, content: bytes, created: datetime
) -> Response:
    """The download, with the header set upstream's ``WriteFileDownload`` produces.

    ``Cache-Control: no-cache`` (not the API-wide ``no-store``) is deliberate upstream:
    it permits caching but forces revalidation, which is what makes the 304 below useful.
    The app's cache-control middleware leaves an already-set header alone so this survives.
    """
    content_type = mime or "application/octet-stream"
    # The three headers every outcome keeps, including both 416s. Cache-Control,
    # Last-Modified and Accept-Ranges are NOT in this set: a 416 sheds all three, which is
    # only visible if you compare the headers on the error responses too.
    base = {
        "Content-Disposition": content_disposition(name),
        "Content-Type": content_type,
        # Never let a browser sniff a type other than the one we recorded.
        "X-Content-Type-Options": "nosniff",
    }

    size = len(content)
    try:
        spans = parse_range(request.headers.get("range", ""), size)
    except RangeNotSatisfiableError as exc:
        headers = dict(base)
        if exc.content_range is not None:
            headers["Content-Range"] = exc.content_range
        # Upstream's 416 has no Cache-Control at all — not `no-cache`, not the API-wide
        # `no-store`. Nothing else in Calton needs this, hence the opt-out rather than a
        # rule.
        request.scope[SUPPRESS_CACHE_CONTROL] = True
        return Response(exc.body, status_code=416, headers=headers)

    revalidated = {**base, "Cache-Control": "no-cache", "Last-Modified": _http_date(created)}

    if_modified_since = _parse_http_date(request.headers.get("if-modified-since", ""))
    if if_modified_since is not None:
        stamp = created if created.tzinfo else created.replace(tzinfo=UTC)
        # Second resolution: the header carries no sub-second part, so compare truncated.
        if stamp.replace(microsecond=0) <= if_modified_since:
            # A 304 carries neither Content-Type nor Accept-Ranges — measured. Sending
            # them is harmless to a browser and still a byte-level parity failure.
            headers = {k: v for k, v in revalidated.items() if k != "Content-Type"}
            return Response(status_code=304, headers=headers)

    headers = {**revalidated, "Accept-Ranges": "bytes"}
    if len(spans) == 1:
        start, end = spans[0]
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(content[start : end + 1], status_code=206, headers=headers)
    if len(spans) > 1:
        body, boundary = _multipart_ranges(content, spans, content_type)
        headers["Content-Type"] = f"multipart/byteranges; boundary={boundary}"
        return Response(body, status_code=206, headers=headers)

    return Response(content, status_code=200, headers=headers)


def _multipart_ranges(
    content: bytes, spans: list[tuple[int, int]], content_type: str
) -> tuple[bytes, str]:
    """``multipart/byteranges`` for a multi-span Range, as ``http.ServeContent`` sends it.

    The boundary is random on both sides, so this body can never be compared byte for byte
    — implemented anyway because the alternative is answering 200 with the whole file,
    which is a divergence a client would actually notice. No client in Phase 1 asks for
    multiple ranges; this exists so that if one ever does, it is not silently wrong.
    """
    import secrets

    boundary = secrets.token_hex(30)
    size = len(content)
    out = bytearray()
    for start, end in spans:
        out += f"\r\n--{boundary}\r\n".encode()
        out += f"Content-Type: {content_type}\r\n".encode()
        out += f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n".encode()
        out += content[start : end + 1]
    out += f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), boundary


#: Registered so an API token can reach these. Measured on `GET /routes`: the group is
#: `tasks_attachments` with actions create / read_all / read_one / delete — a route left
#: out here is refused for every token while JWT callers are unaffected, so the omission
#: only ever shows up under a token.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/tasks/{task}/attachments"),
    ("PUT", "/api/v1/tasks/{task}/attachments"),
    ("GET", "/api/v1/tasks/{task}/attachments/{attachment}"),
    ("DELETE", "/api/v1/tasks/{task}/attachments/{attachment}"),
)
