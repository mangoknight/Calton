"""Where an attachment's bytes live (``pkg/files``).

**The path is the file's id, nothing else.** ``files.basepath`` (default ``files``,
resolved relative to the process's working directory) holds one flat file per row in
``files``, named for its numeric id — no directories, no extension, no hash of the
original name. Measured: uploading ``hello.txt`` as file id 2 produces ``<basepath>/2``.
Storing under the client's filename instead would be both a divergence and a path
traversal, since the name comes from the multipart header unmodified.

The size limit is the one place upstream's configuration and its error message disagree,
and both halves are reproduced verbatim — see ``MAX_SIZE_BYTES`` and
``configured_size_for_message``.
"""

from __future__ import annotations

from pathlib import Path

#: ``files.maxsize`` is parsed by ``datasize`` into whole **megabytes** and multiplied
#: back up (``config.GetMaxFileSizeInMBytes``, files.go:145-152), so the default "20MB"
#: enforces 20 MiB. Measured: a 21 MiB upload is refused, and the limit is applied to the
#: bytes actually read, not to the size the client declares.
MAX_SIZE_MB = 20
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024


def configured_size_for_message() -> int:
    """The number that goes in the 4012 message — which is **0**, not the real limit.

    Upstream builds that message with ``config.FilesMaxSize.GetInt64()``, and the config
    value is the string ``"20MB"``; ``GetInt64`` cannot parse it and yields 0. So a
    refused upload reports "exceeds the configured file size of **0** bytes" while the
    limit actually applied is 20 MiB, from a different accessor.

    Copied rather than corrected (upstream quirks are reproduced): a client that parses
    this number to show "max 20MB" is already broken against the real server, and printing
    the true limit here would be the only place in the API where our number differs.
    ``test_too_large_message_reports_zero_not_the_real_limit`` stops it being "fixed".
    """
    return 0


class FileStorage:
    """Reads and writes attachment bytes under ``basepath``."""

    def __init__(self, basepath: str | Path) -> None:
        self._root = Path(basepath)

    def path_for(self, file_id: int) -> Path:
        return self._root / str(file_id)

    def save(self, file_id: int, content: bytes) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self.path_for(file_id).write_bytes(content)

    def load(self, file_id: int) -> bytes | None:
        """The bytes, or ``None`` when the row exists but the file does not.

        Callers must handle ``None``. Upstream answers **500** here (measured on the
        seed's fixture attachment 1, whose ``files`` row has never had bytes on disk);
        that 500 is upstream losing control rather than a designed response, so it is
        deliberately not reproduced — see the download handler.
        """
        target = self.path_for(file_id)
        try:
            return target.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None

    def delete(self, file_id: int) -> None:
        self.path_for(file_id).unlink(missing_ok=True)


#: Magic-number prefixes, longest first so ``zip`` cannot shadow a container that starts
#: with the same bytes. Only formats measured or plausibly uploaded are listed; anything
#: unrecognised falls through to the text/binary split below.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"OggS", "application/ogg"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)


def detect_mime(content: bytes) -> str:
    """The mime type upstream stores for an uploaded file.

    ⚠️ **An approximation, and the one place in T32 that is not a faithful port.** Upstream
    sniffs content with ``gabriel-vasile/mimetype``, a library with a large decision tree
    that has no Python equivalent. This reproduces the cases measured against the
    reference server and nothing more:

        b"hello attachment world"        -> "text/plain; charset=utf-8"
        b""                              -> "text/plain"          (no charset, measured)
        b"\\x00\\x01\\x02BBBB"             -> "application/octet-stream"
        b"<html><body>hi</body></html>"  -> "text/html; charset=utf-8"
        b'{"a":1}'                       -> "application/json"
        PNG magic                        -> "image/png"

    The mime is content-sniffed, **not** taken from the multipart part's declared
    Content-Type: every sample above was uploaded as ``application/octet-stream`` and
    still came back as shown. An implementation that trusts the client's header agrees
    with upstream only by accident.

    A file whose type is outside this table gets a plausible answer rather than the exact
    one upstream would give. That is a known, bounded gap — recorded here rather than
    hidden — and it only affects the stored ``mime`` string and the download's
    Content-Type, never whether the upload succeeds.
    """
    if not content:
        # Measured: the empty file is "text/plain" with no charset parameter, which is the
        # one case that does not follow the text rule below.
        return "text/plain"

    for prefix, mime in _MAGIC:
        if content.startswith(prefix):
            return mime

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"

    # NUL bytes make it binary even when it decodes as text.
    if "\x00" in text:
        return "application/octet-stream"

    stripped = text.lstrip()
    if stripped[:1] in ("{", "[") and _looks_like_json(stripped):
        return "application/json"
    lowered = stripped[:512].lower()
    if lowered.startswith(("<!doctype html", "<html", "<head", "<body")):
        return "text/html; charset=utf-8"
    if stripped.startswith("<?xml"):
        return "text/xml; charset=utf-8"
    return "text/plain; charset=utf-8"


def _looks_like_json(text: str) -> bool:
    import json

    try:
        json.loads(text)
    except ValueError:
        return False
    return True
