"""``/tokens`` — minting, listing and deleting API tokens.

⚠️ These routes are JWT-only (enforced in ``auth/deps.py``). If an API token
could reach them, one leaked read-only token would be enough to mint a
full-permission one, and the whole permission model would be decorative.

⚠️ **The plaintext appears exactly once**, in the creation response. Only the
PBKDF2 digest is stored, so listing cannot show it and must not try — the
measured list body has no ``token`` key.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from calton.auth import api_token
from calton.auth.deps import CurrentSubject
from calton.core.errors import CaltonError
from calton.core.pagination import Paginator
from calton.db.session import get_db
from calton.db.types import ZERO_TIME, GoValid, Timestamp
from calton.models.api_token import APIToken
from calton.schemas.auth import MessageResponse

DELETED_MESSAGE = "Successfully deleted."


class CreateTokenRequest(BaseModel):
    """All three fields carry ``valid:"required"`` upstream (api_tokens.go:23-33).

    ⚠️ Each one needs a **default** as well as the tag, and the pair is what makes
    the wording right. Go decodes a missing key to the zero value and validates
    afterwards, so upstream cannot tell "absent" from "empty" — leaving a field
    required in Pydantic instead makes a missing key a ``missing`` error, which
    reports the bare field name rather than ``"<field>: non zero value required"``.
    That is precisely the shortfall these two lines close: measured, upstream
    answers ``["expires_at: non zero value required"]``, and we answered
    ``["expires_at"]``.

    ⚠️ ``title: ""`` must fail. It is a *present* key holding the zero value, and
    govalidator rejects it exactly as it rejects an absent one — measured,
    ``{"title": ""}`` comes back with all **three** fields listed, not two.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    title: Annotated[str, GoValid("required")] = Field(default="", validate_default=True)
    permissions: Annotated[dict[str, list[str]], GoValid("required")] = Field(
        default_factory=dict, validate_default=True
    )
    # Timestamp, not datetime: strict mode would refuse the RFC3339 string every
    # client actually sends. The shared type carries the BeforeValidator that
    # performs the conversion Go's binder performs.
    #
    # The default is Go's zero time rather than None: `required` is a zero-value
    # test, so the absent case has to arrive at the validator *as* the zero value
    # to be reported the way upstream reports it.
    expires_at: Annotated[Timestamp, GoValid("required")] = Field(
        default=ZERO_TIME, validate_default=True
    )


class TokenResponse(BaseModel):
    """A token as listed. No ``token`` field — the plaintext is unrecoverable."""

    id: int
    title: str
    permissions: dict[str, list[str]]
    expires_at: Timestamp
    created: Timestamp
    owner_id: int


class CreatedTokenResponse(BaseModel):
    """Creation only: carries the plaintext, the one time it is ever available.

    ⚠️ **Deliberately not a subclass of TokenResponse, and deliberately not DRY.**
    Measured, upstream puts ``token`` third — between ``title`` and ``permissions``:

        id, title, token, permissions, expires_at, created, owner_id

    Pydantic orders inherited fields before new ones, so ``class
    CreatedTokenResponse(TokenResponse)`` emits ``token`` **last**, and the wire bytes
    differ from upstream's on every creation.

    ☠️ The obvious repair — keep the subclass and re-declare all seven fields in the
    right order — **does not work, and looks like it does**. Pydantic keeps the
    parent's position for any inherited name and only appends the genuinely new one,
    so the rewritten subclass still ends ``…, owner_id, token``. Verified rather than
    assumed:

        subclass with every field rewritten -> [id, title, permissions, …, owner_id, token]
        flat model                          -> [id, title, token, permissions, …, owner_id]

    ☠️ And nothing local catches it: ``model_dump()`` of the two compares **equal**
    (dict equality ignores order) while ``model_dump_json()`` differs. So the unit
    tests pass, and only the byte-level parity run sees it — which is exactly the
    shape that makes "I rewrote the fields, it must be fixed now" survive review.

    The duplication is the price of the field order. If these two ever need to share
    logic, share a helper — not a base class.
    """

    id: int
    title: str
    token: str
    permissions: dict[str, list[str]]
    expires_at: Timestamp
    created: Timestamp
    owner_id: int


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/tokens", response_model=list[TokenResponse])
    def list_tokens(
        subject: CurrentSubject,
        db: Annotated[DbSession, Depends(get_db)],
        paginator: Annotated[Paginator, Depends()],
    ) -> Response:
        """Unlike ``/users``, this one goes through the shared paginated path.

        Measured: it sends both ``x-pagination-*`` headers plus the expose
        header, and an empty result is ``[]`` rather than ``null``. Using the
        T05 base rather than setting the headers here is what keeps the two
        list styles from drifting apart.
        """
        owned = select(APIToken).where(APIToken.owner_id == subject.user.id)
        total = db.scalar(select(func.count()).select_from(owned.subquery())) or 0

        windowed = owned.order_by(APIToken.id).offset(paginator.offset)
        if paginator.limit > 0:
            windowed = windowed.limit(paginator.limit)

        rows = [_as_response(row).model_dump(mode="json") for row in db.scalars(windowed).all()]
        return paginator.response(rows, total_items=total)

    @router.put("/tokens", response_model=CreatedTokenResponse, status_code=201)
    def create_token(
        body: CreateTokenRequest,
        subject: CurrentSubject,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> CreatedTokenResponse:
        minted = api_token.mint()
        row = APIToken(
            title=body.title,
            token_salt=minted.salt,
            token_hash=minted.hash,
            token_last_eight=minted.last_eight,
            permissions=api_token.encode_permissions(body.permissions),
            expires_at=body.expires_at,
            owner_id=subject.user.id,
        )
        db.add(row)
        db.commit()

        return CreatedTokenResponse(**_as_response(row).model_dump(), token=minted.plaintext)

    @router.delete("/tokens/{tokenID}", response_model=MessageResponse)
    def delete_token(
        subject: CurrentSubject,
        db: Annotated[DbSession, Depends(get_db)],
        # Aliased to `tokenID`: that is the parameter name the upstream
        # contract declares, and the contract diff compares names. The value
        # is the numeric row id, not the plaintext.
        token_id: Annotated[int, Path(alias="tokenID")],
    ) -> MessageResponse:
        """Deleting a token that is not yours — or does not exist — is 403.

        ⚠️ Not 404. Measured: an unknown id answers
        ``{"code": 0, "message": "Forbidden"}``, the generic write-denied body.
        Answering 404 would both diverge and disclose which ids exist.
        """
        row = db.get(APIToken, token_id)
        if row is None or row.owner_id != subject.user.id:
            raise CaltonError(code=0, message="Forbidden", http_status=403)

        db.delete(row)
        db.commit()
        return MessageResponse(message=DELETED_MESSAGE)

    return router


def _as_response(row: APIToken) -> TokenResponse:
    return TokenResponse(
        id=row.id,
        title=row.title,
        permissions=_sorted_permissions(api_token.granted_permissions(row)),
        expires_at=row.expires_at,
        created=row.created,
        owner_id=row.owner_id,
    )


def _sorted_permissions(permissions: dict[str, list[str]]) -> dict[str, list[str]]:
    """Group keys in alphabetical order, because upstream's is a **map**.

    ⚠️ Not "aligned to a declaration order" — there is no declaration to align to.
    ``permissions`` is a Go ``map[string][]string``, and ``encoding/json`` sorts map
    keys on the way out, so upstream's order is a *property of the serialiser* rather
    than of any struct.

    Measured, which is what separates the two explanations: a token created with
    ``{tasks, projects, labels}`` comes back ``{labels, projects, tasks}`` — equal to
    ``sorted()`` and **not** equal to the order it was sent in. A three-key sample in a
    non-alphabetical order is what makes those two answers distinguishable; with an
    already-sorted sample both hypotheses fit and the wrong one is the tempting kind,
    because "keep the client's order" is what a dict does for free.

    So the fix has to be a sort, not a fixed order. An implementation that pinned some
    declaration order would agree with upstream only for as long as the groups in play
    happened to be alphabetical.

    The action lists are **not** sorted: those are JSON arrays on both sides, and array
    order is contractual. Only the map keys move.
    """
    return {group: permissions[group] for group in sorted(permissions)}
