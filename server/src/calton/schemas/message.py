"""``models.Message`` — the ``{"message": "..."}`` body successful deletes return.

Upstream answers 200 with this object rather than an empty 204 (``delete.go:79``), and the
swagger documents the field, so it needs a declared schema like any other response: a
handler returning a bare ``JSONResponse`` documents nothing, and the contract diff then
has nothing to compare.
"""

from __future__ import annotations

from calton.schemas.base import CaltonModel


class Message(CaltonModel):
    message: str
