"""Task permissions.

Tasks hold no permissions of their own: every check resolves the task's project and asks
there (``tasks_permissions.go:42-87``). Reading a task needs read on its project, and
every mutation needs write — there is no task-level admin.

Moving a task between projects is the one case that consults two projects: the
destination is checked for write *before* the move, so a user cannot relocate a task into
a project they cannot write to.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from calton.models import base_task_query
from calton.models.task import Task
from calton.permissions.project import can_read as project_can_read
from calton.permissions.project import can_write as project_can_write


def _project_of(session: Session, task_id: int) -> int | None:
    """The task's project, or None when the task does not exist or is deleted.

    Goes through ``base_task_query`` so a soft-deleted task is invisible here too —
    resolving permissions for one would let a deleted task be read or edited.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    return None if task is None else task.project_id


def can_read(session: Session, user_id: int, task_id: int) -> tuple[bool, int]:
    """``(can_read, max_permission)``, taken from the task's project."""
    project_id = _project_of(session, task_id)
    if project_id is None:
        return False, 0

    return project_can_read(session, user_id, project_id)


def can_write(session: Session, user_id: int, task_id: int) -> bool:
    project_id = _project_of(session, task_id)
    if project_id is None:
        return False

    return project_can_write(session, user_id, project_id)


#: Creating, updating and deleting a task are all the same check upstream.
can_update = can_write
can_delete = can_write


def can_move(session: Session, user_id: int, task_id: int, destination_project_id: int) -> bool:
    """Whether the task may be moved into ``destination_project_id``.

    Both sides are required: write on the project the task is in now, and write on the one
    it is going to. Checking only the source would let a user push tasks into projects
    they have no access to.
    """
    if not can_write(session, user_id, task_id):
        return False

    return project_can_write(session, user_id, destination_project_id)
