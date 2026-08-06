"""The three sharing creates: a user grant, a team grant, and a link share.

All three require **admin** on the project, and all three answer the CRUD pipeline's
403/0 when refused. The error order differs between them, and it is measured:

    /users  missing project 404/3001 -> not admin 403/0 -> unknown user 404/1005
            -> already granted 409/7002
    /teams  missing project 404/3001 -> not admin 403/0 -> unknown team 404/6002
            -> already granted 409/6004
    /shares missing project 404/3001 -> not admin 403/0

⚠️ On both grant routes an **empty body** takes the "unknown subject" branch rather than a
validation error — 1005 for users, 6002 for teams — because the lookup runs on the zero
value before anything validates. Making either of them 412 is the natural reading of a
required field and is a status these routes never emit.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.auth.password import verify_password
from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.base import utcnow
from calton.models import File, LinkShare, Project, ProjectUser, Team, TeamProject, User
from calton.permissions import project as project_permissions
from calton.schemas.sharing import LinkShareWrite, ProjectTeamWrite, ProjectUserWrite
from calton.services.project_crud import load_project

#: Upstream generates a 40-character URL-safe hash for a share link.
_HASH_LENGTH = 40
_HASH_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _require_admin(session: Session, user_id: int, project_id: int) -> None:
    """Admin on the project, with the project looked up first.

    The order matters and is measured: a project that does not exist is 404/3001 even for
    a caller who could never have administered it, so existence is reported before rights.
    """
    load_project(session, project_id)
    if not project_permissions.is_admin(session, user_id, project_id):
        raise ForbiddenError()


def _require_write(session: Session, user_id: int, project_id: int) -> None:
    """⚠️ **Write, not admin — and only the link-share create is like this.**

    The full ladder, measured twice independently (coder-e first, then me, on a fresh
    project so no seeded grant interferes):

        caller's permission   PUT /shares   GET /shares   PUT /teams   PUT /users
        none                      403           403           403          403
        read  (0)                 403           403           403          403
        write (1)               **201**         403           403          403
        admin (2)                 201           200           201          201

    So a write-level collaborator can mint a link share — a fresh credential granting
    access to the project — while being unable to *list* the shares that already exist.
    Able to grant, unable to audit, and not an admin.

    ⚠️ Making all four require admin is the natural implementation and **looks more
    correct**, which is why nobody would challenge it in review. This implementation did
    exactly that until coder-e's ladder caught it. My own earlier measurement used only a
    read-level user, which is 403 under both rules — the sample could not see the one row
    that separates them.
    """
    load_project(session, project_id)
    if not project_permissions.can_write(session, user_id, project_id):
        raise ForbiddenError()


def _validate_permission(value: int) -> None:
    """0, 1 or 2. Anything else is 400/2004 — the binder's error, not a 412."""
    if value not in (0, 1, 2):
        raise CaltonError.from_name(
            "models.ErrInvalidModel", message="Invalid model provided: Bad Request"
        )


def grant_to_user(
    session: Session, user_id: int, *, project_id: int, body: ProjectUserWrite
) -> ProjectUser:
    _require_admin(session, user_id, project_id)
    _validate_permission(body.permission)

    target = session.scalars(select(User).where(User.username == body.username)).first()
    if target is None:
        # Also the empty-body case: "" matches no user, so a missing username reports
        # 1005 rather than a validation error. See the module docstring.
        raise CaltonError.from_name("user.ErrUserDoesNotExist")

    existing = session.scalars(
        select(ProjectUser).where(
            ProjectUser.project_id == project_id, ProjectUser.user_id == target.id
        )
    ).first()
    project = load_project(session, project_id)
    if existing is not None or project.owner_id == target.id:
        # The owner already has access by owning it, so granting it again is the same
        # 409 as a duplicate row — measured, and it is not an obvious answer.
        raise CaltonError.from_name("models.ErrUserAlreadyHasAccess")

    now = utcnow()
    grant = ProjectUser(
        project_id=project_id,
        user_id=target.id,
        permission=body.permission,
        created=now,
        updated=now,
    )
    session.add(grant)
    session.flush()
    return grant


def grant_to_team(
    session: Session, user_id: int, *, project_id: int, body: ProjectTeamWrite
) -> TeamProject:
    _require_admin(session, user_id, project_id)
    _validate_permission(body.permission)

    if session.get(Team, body.team_id) is None:
        # Empty body lands here too: team 0 does not exist, so 6002 rather than a 412.
        raise CaltonError.from_name("models.ErrTeamDoesNotExist")

    existing = session.scalars(
        select(TeamProject).where(
            TeamProject.project_id == project_id, TeamProject.team_id == body.team_id
        )
    ).first()
    if existing is not None:
        raise CaltonError.from_name("models.ErrTeamAlreadyHasAccess")

    now = utcnow()
    grant = TeamProject(
        project_id=project_id,
        team_id=body.team_id,
        permission=body.permission,
        created=now,
        updated=now,
    )
    session.add(grant)
    session.flush()
    return grant


def create_link_share(
    session: Session, user_id: int, *, project_id: int, body: LinkShareWrite
) -> LinkShare:
    """⚠️ ``sharing_type`` is **derived from whether a password was given**, not sent.

    1 without, 2 with. A client that sends its own ``sharing_type`` has it ignored, and an
    implementation that trusted the body would let a caller advertise a password-protected
    link that has no password.
    """
    _require_write(session, user_id, project_id)
    _validate_permission(body.permission)

    now = utcnow()
    share = LinkShare(
        hash="".join(secrets.choice(_HASH_ALPHABET) for _ in range(_HASH_LENGTH)),
        name=body.name,
        project_id=project_id,
        permission=body.permission,
        sharing_type=2 if body.password else 1,
        password=body.password or None,
        shared_by_id=user_id,
        created=now,
        updated=now,
    )
    session.add(share)
    session.flush()
    return share


def _read_required(session: Session, user_id: int, project_id: int) -> None:
    """``read`` on the project for the listing routes (``GET /projects/{id}/{users,
    teams}``). Refusal is **403/3004** (defined upstream as the read-access error),
    not the 403/0 of the modifies paths nor the 403/1 of the GET list path's
    sibling on a project the caller does not own."""
    load_project(session, project_id)  # raise 404/3001 first, same as PUT
    can_read, _ = project_permissions.can_read(session, user_id, project_id)
    if not can_read:
        raise CaltonError.from_name("models.ErrNeedToHaveProjectReadAccess")


def list_users_for_project(session: Session, user_id: int, *, project_id: int) -> list[ProjectUser]:
    """The user grants on a project, ordered by their ``users_projects`` row id.

    The wire shape echoes the **user**, not the relation row — measured against the
    reference: ``id`` is the user id, ``created``/``updated`` are the user's, and
    ``permission`` carries the access level. The PUT route answers the opposite: a
    relation-row id and the relation-row's timestamps. See ``schemas/sharing``.
    """
    _read_required(session, user_id, project_id)
    return list(
        session.scalars(
            select(ProjectUser).where(ProjectUser.project_id == project_id).order_by(ProjectUser.id)
        )
    )


def list_teams_for_project(session: Session, user_id: int, *, project_id: int) -> list[TeamProject]:
    """The team grants on a project, ordered by their row id."""
    _read_required(session, user_id, project_id)
    return list(
        session.scalars(
            select(TeamProject).where(TeamProject.project_id == project_id).order_by(TeamProject.id)
        )
    )


def list_link_shares(session: Session, user_id: int, *, project_id: int) -> list[LinkShare]:
    """The link shares on a project, ordered by id.

    ⚠️ Permission refusal is the **403 code 1** body, distinct from the 403/3004
    ``/users`` and ``/teams`` give for the same caller. Measured:
    ``GET /projects/{id}/shares`` ReadAll upstream calls ``Project.IsAdmin`` and
    on refusal returns ``models.ErrGenericForbidden`` (the one in
    ``pkg/models/error.go``, code 1) — not ``web.ErrGenericForbidden`` (handler
    core, code 0), which is what the PUT/POST pipeline uses. The two are both
    real and the read/write paths on the same router agree with neither alone.
    """
    load_project(session, project_id)  # 404/3001 first
    if not project_permissions.is_admin(session, user_id, project_id):
        # Same shape as /shares PUT's refusal in §1.1 — except PUT refused on
        # write-and-below (ForbiddenError code 0); GET refuses on non-admin and
        # uses the models-var (code 1). Two refusal shapes, same router.
        raise CaltonError.from_name("models.ErrGenericForbidden")
    return list(
        session.scalars(
            select(LinkShare).where(LinkShare.project_id == project_id).order_by(LinkShare.id)
        )
    )


#: The four grant-mutation siblings (POST update + DELETE for teams and users) and
#: the one-share read/delete all gate on ``_require_admin`` — the same helper the
#: PUT creates use — so a non-admin caller gets the 403/0 body, not the 403/1 the
#: GET list earns. The share-list's code-1 refusal is a ReadAll-only quirk; the
#: ReadOne and DeleteOne handlers upstream return ``(false, nil)`` from
#: ``CanRead``/``CanDelete``, which the web handler maps to ``web.ErrGenericForbidden``
#: (code 0). [INFERENCE] — not measured directly, derived from the ReadAll-vs-Rest
#: split the list's own comment documents.
def get_link_share(session: Session, user_id: int, *, project_id: int, share_id: int) -> LinkShare:
    """``GET /projects/{id}/shares/{share}`` — one link share by id. Admin
    required; a missing share is 404/3006 (``ErrProjectShareDoesNotExist``)."""
    _require_admin(session, user_id, project_id)
    share = session.scalars(
        select(LinkShare).where(LinkShare.id == share_id, LinkShare.project_id == project_id)
    ).first()
    if share is None:
        raise CaltonError.from_name("models.ErrProjectShareDoesNotExist")
    return share


def delete_link_share(session: Session, user_id: int, *, project_id: int, share_id: int) -> None:
    """``DELETE /projects/{id}/shares/{share}`` — admin required; a missing
    share is 404/3006, matching the GET-one lookup. ⚠️ **Admin, not write** —
    distinct from the PUT create, which gates on write (a write-level
    collaborator may mint a share but not remove one). [INFERENCE] per task spec."""
    _require_admin(session, user_id, project_id)
    share = session.scalars(
        select(LinkShare).where(LinkShare.id == share_id, LinkShare.project_id == project_id)
    ).first()
    if share is None:
        raise CaltonError.from_name("models.ErrProjectShareDoesNotExist")
    session.delete(share)
    session.flush()


def update_team_grant(
    session: Session, user_id: int, *, project_id: int, team_id: int, permission: int
) -> TeamProject:
    """``POST /projects/{id}/teams/{teamID}`` — change the permission on an
    existing team grant. Admin required; a missing grant is 403/6007
    (``ErrTeamDoesNotHaveAccessToProject``) — the team exists but is not shared
    with this project, which is a different answer from the 404/6002 a
    non-existent team earns on the create path."""
    _require_admin(session, user_id, project_id)
    _validate_permission(permission)
    grant = session.scalars(
        select(TeamProject).where(
            TeamProject.project_id == project_id, TeamProject.team_id == team_id
        )
    ).first()
    if grant is None:
        raise CaltonError.from_name("models.ErrTeamDoesNotHaveAccessToProject")
    grant.permission = permission
    grant.updated = utcnow()
    session.flush()
    return grant


def delete_team_grant(session: Session, user_id: int, *, project_id: int, team_id: int) -> None:
    """``DELETE /projects/{id}/teams/{teamID}`` — admin required; a missing grant
    is 403/6007."""
    _require_admin(session, user_id, project_id)
    grant = session.scalars(
        select(TeamProject).where(
            TeamProject.project_id == project_id, TeamProject.team_id == team_id
        )
    ).first()
    if grant is None:
        raise CaltonError.from_name("models.ErrTeamDoesNotHaveAccessToProject")
    session.delete(grant)
    session.flush()


def update_user_grant(
    session: Session,
    user_id: int,
    *,
    project_id: int,
    target_user_id: int,
    permission: int,
) -> ProjectUser:
    """``POST /projects/{id}/users/{userID}`` — change the permission on an
    existing user grant. The path's ``userID`` is the user's id (not the username
    the PUT create takes). Admin required; a missing grant is 403/7003
    (``ErrUserDoesNotHaveAccessToProject``)."""
    _require_admin(session, user_id, project_id)
    _validate_permission(permission)
    grant = session.scalars(
        select(ProjectUser).where(
            ProjectUser.project_id == project_id, ProjectUser.user_id == target_user_id
        )
    ).first()
    if grant is None:
        raise CaltonError.from_name("models.ErrUserDoesNotHaveAccessToProject")
    grant.permission = permission
    grant.updated = utcnow()
    session.flush()
    return grant


def delete_user_grant(
    session: Session, user_id: int, *, project_id: int, target_user_id: int
) -> None:
    """``DELETE /projects/{id}/users/{userID}`` — admin required; a missing grant
    is 403/7003."""
    _require_admin(session, user_id, project_id)
    grant = session.scalars(
        select(ProjectUser).where(
            ProjectUser.project_id == project_id, ProjectUser.user_id == target_user_id
        )
    ).first()
    if grant is None:
        raise CaltonError.from_name("models.ErrUserDoesNotHaveAccessToProject")
    session.delete(grant)
    session.flush()


def authenticate_link_share(session: Session, *, share_hash: str, password: str) -> LinkShare:
    """``POST /shares/{share}/auth`` — verify the password (if any) and return
    the share row; the route mints the JWT from it. The share's hash in the path
    is the credential: a missing hash is 400/13003; a password-protected share
    (``sharing_type == 2``) with no password supplied is 412/13001; a wrong
    password is 403/13002. A share with no password authenticates on the hash
    alone."""
    share = session.scalars(select(LinkShare).where(LinkShare.hash == share_hash)).first()
    if share is None:
        raise CaltonError.from_name("models.ErrLinkShareTokenInvalid")
    if share.sharing_type == 2:
        if not password:
            raise CaltonError.from_name("models.ErrLinkSharePasswordRequired")
        if not share.password or not verify_password(password, share.password):
            raise CaltonError.from_name("models.ErrLinkSharePasswordInvalid")
    return share


def upload_project_background(
    session: Session,
    user_id: int,
    *,
    project_id: int,
    filename: str,
    content_type: str,
    size: int,
) -> Project:
    """``PUT /projects/{id}/backgrounds/upload`` — store the uploaded file's
    metadata in the ``files`` table and point ``project.background_file_id`` at
    it. Admin required. The file's bytes are not persisted (the ``files`` table
    holds metadata only in this phase); this is the stub level the task spec
    accepts."""
    _require_admin(session, user_id, project_id)
    file = File(
        name=filename,
        mime=content_type or None,
        size=size,
        created_by_id=user_id,
        created=utcnow(),
    )
    session.add(file)
    session.flush()
    project = load_project(session, project_id)
    project.background_file_id = file.id
    session.flush()
    return project
