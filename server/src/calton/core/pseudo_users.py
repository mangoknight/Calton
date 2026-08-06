"""Resolve a stored ``user_id`` to the wire ``UserRead`` it should serialise as.

Two kinds of id share the ``created_by`` / ``assignees`` / comment-author columns:

* a real ``users.id`` (≥ 0), and
* a **link share's negative id** — upstream writes ``-share.id`` and renders it back
  as a pseudo-user instead of looking it up in the ``users`` table.

Upstream's ``getUsersOrLinkSharesFromIDs`` (pkg/models/users.go:71) is the one place
both kinds pass through, on every resource that exposes a ``created_by`` field:
attachments (pkg/models/task_attachment.go:271, 483), comments (:381 in
task_comments.go), tasks (:717 in tasks.go), and kanban (:144, 195 in kanban.go).
A single lookup that swallows negative ids — the natural thing to write in Python,
where ``session.get(User, -2)`` simply returns ``None`` — leaves the same hole on
all of them at once. The corpus only reaches it through attachments today, so fixing
the attachment alone would re-open the bug on the other three the first time a
comment's author is a link share. The shared resolver is what keeps the fix in one
place; the other three call sites have not been routed through it yet because the
corpus does not exercise a link-share author on them — when it does, the route is
``from calton.core.pseudo_users import resolve_subject``.

The wire shape comes from ``LinkSharing.toUser`` (pkg/models/link_sharing.go:130):

    id       = -share.id
    name     = share.name + " (Link Share)"  if share.name else "Link Share"
    username = "link-share-" + str(share.id)
    created  = share.created
    updated  = share.updated

⚠ Three things that read as details and are not:

* **``id < 0`` is the discriminator, not ``id > 0``** — ``0`` is not a link share. A
  real-but-deleted user id is a non-negative int with no row, and that one *does*
  return ``None`` (upstream swallows it). Collapsing "missing" and "link share" into
  one branch turns every negative id into ``None``, which is exactly the bug.
* **``-1`` and ``-2`` are different shares.** An implementation that maps every
  negative id to one fixed pseudo-user is green on a corpus with only one sample
  and wrong on the other sample. The tests pin both.
* **the pseudo-user's timestamps are the share's**, not zero. A "blank subject"
  implementation would pass the attachment's JWT-only case and fail the read-back
  where the share's real timestamps are expected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.models import LinkShare, User
from calton.schemas.user import UserRead


def _user_to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        name=user.name or "",
        username=user.username or "",
        created=user.created,
        updated=user.updated,
    )


def _link_share_to_read(share: LinkShare) -> UserRead:
    """``LinkSharing.toUser`` (pkg/models/link_sharing.go:130), verbatim in shape."""
    name = share.name or ""
    display = f"{name} (Link Share)" if name else "Link Share"
    return UserRead(
        id=-share.id,
        name=display,
        username=f"link-share-{share.id}",
        created=share.created,
        updated=share.updated,
    )


def resolve_subject(session: Session, user_id: int) -> UserRead | None:
    """One ``user_id`` → its wire ``UserRead``, or ``None`` when neither a user nor a
    link share backs it.

    Negative ids route to ``link_shares`` (with the sign flipped back); non-negative
    ids route to ``users``. A real-but-deleted user row stays ``None`` — upstream
    swallows that lookup, and so does this.
    """
    if user_id < 0:
        share = session.get(LinkShare, -user_id)
        return _link_share_to_read(share) if share is not None else None
    user = session.get(User, user_id)
    return _user_to_read(user) if user is not None else None


def resolve_subjects(session: Session, user_ids: Sequence[int]) -> Mapping[int, UserRead | None]:
    """Bulk form of :func:`resolve_subject` — one round trip per side, never one per
    id. Mirrors upstream's batched ``getUsersOrLinkSharesFromIDs`` so a list endpoint
    does not gain an N+1 by routing through a per-row resolver.

    ``None`` values are kept (not omitted): callers distinguish "this id has no row"
    from "this id was not asked for".
    """
    if not user_ids:
        return {}

    positives: set[int] = {uid for uid in user_ids if uid >= 0}
    negatives: set[int] = {-uid for uid in user_ids if uid < 0}

    users: dict[int, User] = {}
    if positives:
        users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(positives))).all()}
    shares: dict[int, LinkShare] = {}
    if negatives:
        shares = {
            s.id: s
            for s in session.scalars(select(LinkShare).where(LinkShare.id.in_(negatives))).all()
        }

    out: dict[int, UserRead | None] = {}
    for uid in user_ids:
        if uid < 0:
            share = shares.get(-uid)
            out[uid] = _link_share_to_read(share) if share is not None else None
        else:
            user = users.get(uid)
            out[uid] = _user_to_read(user) if user is not None else None
    return out


__all__ = ["resolve_subject", "resolve_subjects"]
