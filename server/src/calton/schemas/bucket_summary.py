"""A bucket without its tasks.

Its own module purely to break an import cycle: ``Bucket.tasks`` holds tasks and
``Task.buckets`` holds buckets, so if both lived together the two schema modules would
import each other and one of them would need rebuilding at exactly the right moment.
Splitting the half that references nothing removes the cycle instead of sequencing it.
"""

from __future__ import annotations

from calton.db.types import GoFloat, Timestamp
from calton.schemas.base import CaltonModel
from calton.schemas.user import UserRead


class BucketSummary(CaltonModel):
    """Measured: the buckets embedded in a task carry no ``tasks`` key at all, which
    falls out of this split rather than needing to be stripped afterwards."""

    id: int
    title: str = ""
    project_view_id: int
    limit: int = 0
    #: The bucket's **total** task count, which does not shrink when ``per_page``
    #: truncates ``tasks``. Two numbers, deliberately: "50 of 60".
    count: int = 0
    position: GoFloat = 0
    created: Timestamp
    updated: Timestamp
    created_by: UserRead | None = None
