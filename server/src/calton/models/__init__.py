"""SQLAlchemy models, one module per upstream table group.

Importing this package registers every model on ``Base.metadata``, which is what Alembic
and the schema parity test rely on. A new model is invisible to both until it is listed
here.

Column names, types, order and index names are matched to the Go schema deliberately —
see ``tests/unit/test_schema_parity.py``.
"""

from calton.models.api_token import APIToken
from calton.models.bucket import Bucket
from calton.models.caldav_token import CalDAVToken
from calton.models.file import File
from calton.models.label import Label, LabelTask
from calton.models.link_share import LinkShare
from calton.models.notification import Notification
from calton.models.project import Project
from calton.models.project_view import ProjectView
from calton.models.reaction import Reaction
from calton.models.saved_filter import Favorite, SavedFilter, Subscription
from calton.models.session import Session
from calton.models.task import Task, base_task_query
from calton.models.task_assignee import TaskAssignee
from calton.models.task_comment import TaskAttachment, TaskComment
from calton.models.task_position import TaskBucket, TaskPosition
from calton.models.task_relation import TaskRelation
from calton.models.task_reminder import TaskReminder
from calton.models.task_unread import TaskUnreadStatus
from calton.models.team import ProjectUser, Team, TeamMember, TeamProject
from calton.models.totp import TOTP
from calton.models.user import User, UserToken
from calton.models.webhook import Webhook

__all__ = [
    "TOTP",
    "APIToken",
    "Bucket",
    "CalDAVToken",
    "Favorite",
    "File",
    "Label",
    "LabelTask",
    "LinkShare",
    "Notification",
    "Project",
    "ProjectUser",
    "ProjectView",
    "Reaction",
    "SavedFilter",
    "Session",
    "Subscription",
    "Task",
    "TaskAssignee",
    "TaskAttachment",
    "TaskBucket",
    "TaskComment",
    "TaskPosition",
    "TaskRelation",
    "TaskReminder",
    "TaskUnreadStatus",
    "Team",
    "TeamMember",
    "TeamProject",
    "User",
    "UserToken",
    "Webhook",
    "base_task_query",
]
