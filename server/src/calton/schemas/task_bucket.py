"""The response to "put this task in that bucket" (``models.TaskBucket``).

Five keys, and **two of them are allowed to contradict each other**. Measured against the
reference server, dragging the repeating task 922 into view 923's done bucket 922::

    {"bucket_id": 920,          # where the task actually ended up
     "bucket": {"id": 922, …},  # the bucket that was *asked* for
     "task_id": 922, "project_view_id": 923, "task": {…}}

The task is not in bucket 922. A repeating task does not stay in the done bucket — it
rolls forward and gets routed back to the view's default bucket (920) — but the embedded
``bucket`` object was resolved from the requested id before that happened and is never
re-read. ``updateTaskBucket`` reassigns ``b.BucketID`` and leaves the ``bucket`` local
alone (kanban_task_bucket.go:146-155, 228).

Which field a client reads therefore decides whether it notices:

* the top-level ``bucket_id`` is the truth — 920, task visibly not completed;
* the nested ``bucket.id`` is the echo — 922, and it is the *more* attractive field to
  read, because it carries the title and the count needed to render a column.

A frontend that renders from the nested object draws the card in "Done", and the next
refresh snaps it back to "To-Do". The user sees a card that bounces, and every response
involved was a 200.

**Both values are reproduced exactly, including their disagreement.** Making
``bucket.id`` agree with ``bucket_id`` reads as an obvious bug fix and is a wire change;
if we ever decide to diverge it goes through the §5.3 exception list, not through a
tidy-up. ``buckettask.move.response_bucket_id_contradicts_nested_bucket`` is the case
that holds this, and it is paired with ``buckettask.move.ok``'s ``body_keys_exactly``:
one pins the key set, the other pins the contradiction between two of them.
"""

from __future__ import annotations

from calton.schemas.base import CaltonModel
from calton.schemas.bucket_summary import BucketSummary
from calton.schemas.task import TaskRead


class TaskBucketRead(CaltonModel):
    """Field order follows the Go struct (kanban_task_bucket.go:33) rather than any
    grouping that would read better — the two ``*_id`` fields are deliberately not
    adjacent, because that is where upstream put them."""

    bucket_id: int
    #: The requested bucket, echoed. May disagree with ``bucket_id`` — see the module
    #: docstring. ``created_by`` is null here even though the same bucket carries a
    #: hydrated user on the list endpoint: this object is assembled from the row alone.
    bucket: BucketSummary | None = None
    task_id: int
    project_view_id: int
    #: The task **after** the move, so a done-state change is already reflected.
    task: TaskRead | None = None
