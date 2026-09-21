"""Backfill ``task_buckets`` for tasks created before create-time placement existed.

Until ``services.task_placement`` was wired into ``create_task``, no task ever received a
``task_buckets`` row, so every manual Kanban rendered empty columns while the list view
showed the tasks. New tasks are placed on create now; this repairs the ones already there.

Data only, and additive only: a task that already has a row for a view — because someone
dragged it, or because the view was created after the task — is left exactly where it is.
A done task goes to the view's done bucket when it has one, everything else to the default
bucket (the configured one, else the view's first by position), which is the choice
``setTasksInBucketInViews`` would have made at create time.

Self-contained SQL on purpose: a migration must keep meaning the same thing after the
service layer it would otherwise import has moved on.

Revision ID: 0004
Revises: fbaa4b62de84
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "fbaa4b62de84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_KIND_KANBAN = 3
BUCKET_MODE_MANUAL = 1


def upgrade() -> None:
    bind = op.get_bind()

    views = bind.execute(
        sa.text(
            "SELECT id, project_id, default_bucket_id, done_bucket_id FROM project_views "
            "WHERE view_kind = :kind AND bucket_configuration_mode = :mode"
        ),
        {"kind": VIEW_KIND_KANBAN, "mode": BUCKET_MODE_MANUAL},
    ).fetchall()

    for view_id, project_id, default_bucket_id, done_bucket_id in views:
        default_bucket = int(default_bucket_id or 0)
        if not default_bucket:
            first = bind.execute(
                sa.text(
                    "SELECT id FROM buckets WHERE project_view_id = :view "
                    "ORDER BY position ASC LIMIT 1"
                ),
                {"view": view_id},
            ).fetchone()
            default_bucket = int(first[0]) if first else 0
        if not default_bucket:
            continue  # a Kanban with no buckets: nowhere to put anything
        done_bucket = int(done_bucket_id or 0)

        orphans = bind.execute(
            sa.text(
                "SELECT t.id, t.done FROM tasks t "
                "WHERE t.project_id = :project AND t.deleted_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM task_buckets tb "
                "WHERE tb.task_id = t.id AND tb.project_view_id = :view)"
            ),
            {"project": project_id, "view": view_id},
        ).fetchall()

        for task_id, done in orphans:
            bucket = done_bucket if (done and done_bucket) else default_bucket
            bind.execute(
                sa.text(
                    "INSERT INTO task_buckets (bucket_id, task_id, project_view_id) "
                    "VALUES (:bucket, :task, :view)"
                ),
                {"bucket": bucket, "task": task_id, "view": view_id},
            )


def downgrade() -> None:
    # Deliberately nothing. The rows this added are indistinguishable from ones a user
    # created by dragging a card, so removing "ours" would mean guessing — and the state
    # being restored is the bug.
    pass
