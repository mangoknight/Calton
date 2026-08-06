"""Task comments: list, read, create, update and delete (T30).

Every rule below was measured against the running Go reference server, not read off
``pkg/models/task_comments.go``.

**The write gate is ``CanWrite(task) AND is-author`` — both halves, not either.** This is
the single thing most likely to be got wrong here, and it can be got wrong in two
opposite directions, each of which looks correct on its own:

* *Author only.* Measured: dave, who **wrote** comment 960 but holds only read on the
  project, gets **403** on both update and delete. Dropping the write check would let a
  demoted collaborator keep editing.
* *Project permission only.* Measured: alice, the project **owner**, gets **403** editing
  or deleting bob's comment 951; so does carol, an admin. Folding comments into the
  project permission model — the natural unification, and the one a product manager will
  ask for — silently lets an administrator rewrite someone else's words.

The corpus (``harness/corpus/_comments.yaml``) covers the second direction only, in
``comment.update.others_comment_403_even_for_project_owner`` and its delete twin, and its
prose says the rule is "author, not project permission". That prose is half the rule; the
first bullet above is measured here and has no corpus case, which is why
``test_comments.py`` carries one.

Gate order is measured too, and it is observable:

1. **Body validation**, before anything else. A 412 comes back for an empty comment even
   when the task does not exist *and* the comment does not exist *and* the caller could
   not write it anyway — measured on all three. A consequence worth knowing: this path
   does not leak whether a task id exists, because a probe with an empty body always
   answers 412.
2. **The task**: absent is 404/4002, unwritable is 403/0.
3. **The comment**, scoped to the task in the path: 404/4015.
4. **Authorship**: 403/0.

Three responses that look like oversights and are contract:

* The **list** answers 403 **code 1** ("You're not allowed to do this.") while **read-one**
  on the same forbidden task answers 403 **code 0** ("You don't have the permission to see
  this"). Same resource, same overreach, two bodies. Any implementation that shares one
  authorisation helper between them unifies these, and both are measured.
* ``GET /tasks/951/comments/950`` — comment 950 exists but belongs to task 950 — is
  **404/4015**, not 200. Looking a comment up by id alone is the obvious shortcut and
  turns this endpoint into a cross-task read oracle.
* The **update** response drops ``author`` and ``created`` when the client did not send
  them, because upstream serialises the struct it bound the request into rather than
  re-reading the row. It is not a fixed null: see ``schemas.task_comment.TaskCommentWrite``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import FORBIDDEN_READ_MESSAGE, ForbiddenError
from calton.models import base_task_query
from calton.models.task import Task
from calton.models.task_comment import TaskComment
from calton.permissions import task as task_permissions
from calton.schemas.task_comment import (
    TaskCommentRead,
    TaskCommentWrite,
    TaskCommentWriteResponse,
)
from calton.services import task_service

#: ``models.ErrGenericForbidden`` — 403 **code 1**, only ever used by the list endpoint
#: here. Everything else on this resource uses the CRUD pipeline's 403 code 0.
_LIST_FORBIDDEN = "models.ErrGenericForbidden"


def _require_task(session: Session, task_id: int) -> Task:
    """The task, or the 404/4002 upstream gives.

    Through ``base_task_query`` so a soft-deleted task reads as absent, matching every
    other task permission check.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    return task


def _comment_missing() -> CaltonError:
    return CaltonError.from_name("models.ErrTaskCommentDoesNotExist")


def _load(session: Session, *, task_id: int, comment_id: int) -> TaskComment:
    """One comment, **scoped to its task**.

    The scoping is the whole point: without the ``task_id`` predicate a caller who can see
    any single task can walk comment ids and read comments on tasks they cannot open, and
    nothing about that failure is visible from the outside.
    """
    comment = session.scalars(
        select(TaskComment).where(TaskComment.id == comment_id, TaskComment.task_id == task_id)
    ).one_or_none()
    if comment is None:
        raise _comment_missing()
    return comment


def _hydrated(session: Session, comment: TaskComment) -> TaskCommentRead:
    """A comment as the read paths return it: author filled in, reactions null."""
    return TaskCommentRead(
        id=comment.id,
        comment=comment.comment or "",
        author=task_service.user_view(session, comment.author_id),
        reactions=None,
        created=comment.created,
        updated=comment.updated,
    )


def list_comments(session: Session, *, task_id: int, user_id: int) -> list[TaskCommentRead]:
    """Every comment on the task, oldest first.

    Ordered by the row id. The seed deliberately interleaves authors (alice 950, bob 951,
    alice 952) so that an ``ORDER BY author_id`` — a plausible way to "group" a thread —
    produces the same set in a different order and only an ordered assertion notices.
    """
    _require_task(session, task_id)
    allowed, _ = task_permissions.can_read(session, user_id, task_id)
    if not allowed:
        raise CaltonError.from_name(_LIST_FORBIDDEN)

    comments = session.scalars(
        select(TaskComment).where(TaskComment.task_id == task_id).order_by(TaskComment.id)
    ).all()
    return [_hydrated(session, comment) for comment in comments]


def read_comment(
    session: Session, *, task_id: int, comment_id: int, user_id: int
) -> tuple[TaskCommentRead, int]:
    """The comment and the caller's permission on it, for ``x-max-permission``.

    The permission is the one held on the *task's project*: ``TaskComment.CanRead``
    delegates straight to ``Task.CanRead`` and passes its second return value through, so
    the header on a comment reports the project's number, not something comment-shaped.
    """
    _require_task(session, task_id)
    allowed, max_permission = task_permissions.can_read(session, user_id, task_id)
    if not allowed:
        # Code 0 with the ReadOne wording, where the list endpoint one function up answers
        # code 1 with different wording. Both measured; do not share a helper.
        raise ForbiddenError(FORBIDDEN_READ_MESSAGE)

    comment = _load(session, task_id=task_id, comment_id=comment_id)
    return _hydrated(session, comment), max_permission


def create_comment(
    session: Session, *, task_id: int, data: TaskCommentWrite, user_id: int
) -> TaskCommentRead:
    """``PUT`` a new comment — 201 with the comment fully hydrated.

    Contrast the update response, which is missing ``author`` and ``created``. Same
    entity, two shapes, because ``Create`` fills the author in and ``Update`` does not.

    A body ``id`` is ignored here — ``Create`` assigns ``tc.ID = 0`` before inserting —
    even though the very same field *does* override the path segment on update. Measured
    both ways.
    """
    _require_task(session, task_id)
    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError()

    comment = TaskComment(comment=data.comment, author_id=user_id, task_id=task_id)
    session.add(comment)
    session.commit()

    return TaskCommentRead(
        id=comment.id,
        comment=comment.comment or "",
        author=task_service.user_view(session, user_id),
        # Echoed rather than fixed at null: ``Reactions`` is ``xorm:"-"``, bound from the
        # body and never overwritten by Create.
        reactions=_echoed_reactions(data),
        created=comment.created,
        updated=comment.updated,
    )


def _require_may_modify(
    session: Session, *, task_id: int, comment_id: int, user_id: int
) -> TaskComment:
    """``canUserModifyTaskComment``: write on the task **and** authorship of the comment.

    The order is load-bearing and measured: a forbidden task answers 403 even when the
    comment id does not exist, and a writable task with an unknown comment answers 404.
    Swapping the two turns the 404 into a permission oracle in one direction and leaks
    comment existence in the other.
    """
    _require_task(session, task_id)
    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError()

    comment = _load(session, task_id=task_id, comment_id=comment_id)
    if comment.author_id != user_id:
        raise ForbiddenError()
    return comment


def update_comment(
    session: Session, *, task_id: int, comment_id: int, data: TaskCommentWrite, user_id: int
) -> TaskCommentWriteResponse:
    """``POST`` — only the ``comment`` column is written (``Cols("comment")``).

    The response is the bound request struct, not a re-read, so ``author`` and ``created``
    come back as whatever the client sent — nothing for the corpus case, the real values
    for a read-modify-write client. ``updated`` is always the server's.
    """
    comment = _require_may_modify(session, task_id=task_id, comment_id=comment_id, user_id=user_id)
    comment.comment = data.comment
    session.commit()

    return TaskCommentWriteResponse(
        id=comment.id,
        comment=data.comment,
        author=data.author,
        reactions=_echoed_reactions(data),
        created=data.created,
        updated=comment.updated,
    )


def delete_comment(session: Session, *, task_id: int, comment_id: int, user_id: int) -> None:
    comment = _require_may_modify(session, task_id=task_id, comment_id=comment_id, user_id=user_id)
    session.delete(comment)
    session.commit()


def _echoed_reactions(data: TaskCommentWrite) -> dict[str, object] | None:
    """The request's ``reactions`` map, serialised back out.

    Kept as a function so both write paths echo it identically; upstream never populates
    it on either, so a value here can only be one the client sent.
    """
    if data.reactions is None:
        return None
    return {
        key: [user.model_dump(mode="json") for user in users]
        for key, users in data.reactions.items()
    }
