"""``teams``, ``team_members``, ``team_projects`` and ``users_projects``.

``team_projects.permission`` and ``users_projects.permission`` are the two grant tables
the permission CTE reads (T11). The column is already named ``permission`` upstream; the
old ``right`` spelling only survives on the wire, where T19 double-writes it.

Note the table is ``users_projects``, not ``project_users`` — the API route is
``/projects/{id}/projectusers``, which makes the wrong name easy to reach for.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column
from calton.db.types import CaltonBoolean


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = created_column(nullable=True)
    updated: Mapped[datetime] = updated_column(nullable=True)
    # Upstream added this after the timestamps, so it sits last.
    is_public: Mapped[bool] = mapped_column(CaltonBoolean, nullable=False, server_default=text("0"))

    __table_args__ = (
        Index("UQE_teams_id", "id", unique=True),
        Index("IDX_teams_created_by_id", "created_by_id"),
        {"sqlite_autoincrement": True},
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    admin: Mapped[bool | None] = mapped_column(CaltonBoolean, nullable=True)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_team_members_id", "id", unique=True),
        Index("IDX_team_members_team_id", "team_id"),
        Index("IDX_team_members_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )


class TeamProject(Base):
    """A team's grant on a project. The CTE takes MAX(permission) across a user's teams."""

    __tablename__ = "team_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    permission: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_team_projects_id", "id", unique=True),
        Index("IDX_team_projects_team_id", "team_id"),
        Index("IDX_team_projects_project_id", "project_id"),
        Index("IDX_team_projects_permission", "permission"),
        {"sqlite_autoincrement": True},
    )


class ProjectUser(Base):
    """A user's direct grant on a project. Permission is Read=0, Write=1, Admin=2."""

    __tablename__ = "users_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    permission: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_users_projects_id", "id", unique=True),
        Index("IDX_users_projects_user_id", "user_id"),
        Index("IDX_users_projects_project_id", "project_id"),
        Index("IDX_users_projects_permission", "permission"),
        {"sqlite_autoincrement": True},
    )
