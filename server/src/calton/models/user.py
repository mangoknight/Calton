"""``users`` and ``user_tokens`` (``pkg/user/user.go``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column
from calton.db.types import CaltonBoolean, CaltonDateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("0"))
    is_admin: Mapped[bool] = mapped_column(CaltonBoolean, nullable=False, server_default=text("0"))
    avatar_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issuer: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_reminders_enabled: Mapped[bool | None] = mapped_column(
        CaltonBoolean, nullable=True, server_default=text("1")
    )
    discoverable_by_name: Mapped[bool | None] = mapped_column(
        CaltonBoolean, nullable=True, server_default=text("0")
    )
    discoverable_by_email: Mapped[bool | None] = mapped_column(
        CaltonBoolean, nullable=True, server_default=text("0")
    )
    overdue_tasks_reminders_enabled: Mapped[bool | None] = mapped_column(
        CaltonBoolean, nullable=True, server_default=text("1")
    )
    overdue_tasks_reminders_time: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'09:00'")
    )
    default_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bot_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    week_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_scheduled_at: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    deletion_last_reminder_sent: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    frontend_settings: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_settings_links: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_users_id", "id", unique=True),
        Index("UQE_users_username", "username", unique=True),
        Index("IDX_users_discoverable_by_name", "discoverable_by_name"),
        Index("IDX_users_discoverable_by_email", "discoverable_by_email"),
        Index("IDX_users_overdue_tasks_reminders_enabled", "overdue_tasks_reminders_enabled"),
        Index("IDX_users_default_project_id", "default_project_id"),
        Index("IDX_users_bot_owner_id", "bot_owner_id"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )


class UserToken(Base):
    __tablename__ = "user_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_user_tokens_id", "id", unique=True),
        Index("IDX_user_tokens_token", "token"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
