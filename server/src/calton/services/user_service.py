"""User lookup and registration.

The search rules were measured rather than inferred, because the obvious reading
("``s`` does a LIKE over username and name") is wrong in a way that leaks user
accounts. What the reference server actually does:

===========================  ===================================================
exact username match         always finds the user, whatever their flags say
``discoverable_by_name``     enables **substring** matching on username *or* name
``discoverable_by_email``    enables **exact** email matching — substrings do not
empty or absent ``s``        finds nothing at all
===========================  ===================================================

Two of those are easy to get wrong in the permissive direction. Substring
matching on email would let anyone enumerate addresses by walking prefixes, and
returning every user for an empty ``s`` would turn the endpoint into a directory
dump. Both changes would look like reasonable "search" behaviour in review.

Every function takes the session as its first positional parameter and never
opens its own — the Policy/CrudService contract, and what makes read-your-writes
hold within a request.
"""

from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session as DbSession

from calton.auth.password import hash_password
from calton.core.errors import CaltonError, ValidationError
from calton.models.user import User


def search_users(session: DbSession, term: str | None) -> list[User]:
    """Users discoverable under ``term``. Empty for a blank or missing term."""
    if not term:
        return []

    return list(session.scalars(_search_query(term)).all())


def _search_query(term: str) -> Select[tuple[User]]:
    substring = f"%{term}%"

    return (
        select(User)
        .where(
            or_(
                # Exact username: not gated on any flag. Someone who already
                # knows the username learns nothing new.
                User.username == term,
                # Substring over username and name, gated on the name flag.
                (User.discoverable_by_name == 1)
                & or_(User.username.like(substring), User.name.like(substring)),
                # Email must match in full. A LIKE here would let an attacker
                # recover addresses one character at a time.
                (User.discoverable_by_email == 1) & (User.email == term),
            )
        )
        .order_by(User.id)
    )


#: bcrypt_password, measured at the boundary: seven characters is refused, eight
#: is accepted.
MIN_PASSWORD_LENGTH = 8

#: The ``username`` validator is far laxer than it looks. ``@``, ``/``, ``:``,
#: ``#``, ``+``, ``_``, ``-``, uppercase and non-ASCII are all accepted; only
#: whitespace and a literal dot are refused. Recorded as a matrix in
#: ``go_users.json.register.validation.usernames`` — do not "tighten" this to an
#: alphanumeric rule, which would reject usernames upstream lets people hold and
#: make an imported database partially unusable.
FORBIDDEN_USERNAME_CHARACTERS = "."


def _invalid_username(username: str) -> bool:
    return any(character.isspace() for character in username) or any(
        character in username for character in FORBIDDEN_USERNAME_CHARACTERS
    )


def _invalid_email(email: str) -> bool:
    local, separator, domain = email.partition("@")
    return not (local and separator and "." in domain and not domain.startswith("."))


def _validation_failure(field: str, value: str, validator: str) -> str:
    """One ``invalid_fields`` entry, in the reference server's wording.

    ⚠️ The value is echoed back — for a password that means the plaintext appears
    in the response body. That is upstream's format and parity requires it, but
    it means anything logging error bodies logs passwords. Registered as an
    upstream quirk rather than silently "fixed", which would diverge.
    """
    return f"{field}: {value} does not validate as {validator}"


def register_user(
    session: DbSession, *, username: str | None, password: str | None, email: str | None
) -> User:
    """Create an account, or raise the error the reference server raises.

    Check order is measured and matters, because several of these can be true at
    once: presence (400/1004) → field validation (412/2002) → duplicate username
    (400/1001) → duplicate email (400/1002). A registration with both an invalid
    username and a taken email reports the invalid username.

    ⚠️ A missing email raises ``ErrNoUsernamePassword`` — "Please specify a
    username and a password." The message does not mention email even though
    email is what is missing. Measured; copied rather than corrected, because the
    string is what clients match on.
    """
    if not username or not password or not email:
        raise CaltonError.from_name("user.ErrNoUsernamePassword")

    # Reported together and ordered by field name, as the reference server does.
    invalid_fields = []
    if _invalid_email(email):
        invalid_fields.append(_validation_failure("email", email, "email"))
    if len(password) < MIN_PASSWORD_LENGTH:
        invalid_fields.append(_validation_failure("password", password, "bcrypt_password"))
    if _invalid_username(username):
        invalid_fields.append(_validation_failure("username", username, "username"))
    if invalid_fields:
        raise ValidationError(invalid_fields)

    if session.scalars(select(User).where(User.username == username)).one_or_none():
        raise CaltonError.from_name("user.ErrUsernameExists")

    if session.scalars(select(User).where(User.email == email)).one_or_none():
        raise CaltonError.from_name("user.ErrUserEmailExists")

    user = User(
        username=username,
        password=hash_password(password),
        email=email,
        is_admin=False,
        # Not nullable in the Go schema, and it has no server default on insert
        # through the ORM, so it is set explicitly.
        overdue_tasks_reminders_time="09:00",
    )
    session.add(user)
    session.flush()

    # Measured: immediately after POST /register, GET /user reports
    # settings.default_project_id as a real project id, titled "Inbox", already
    # carrying the four default views. Registration is not just the row.
    #
    # Imported here rather than at module scope: project_service imports from this
    # module, and a top-level import closes the cycle at import time.
    #
    # ⚠️ Registration upstream also creates a "My Open Tasks" saved filter, visible as
    # pseudo project -953 in the new user's project list. That is T29's and is not done
    # here — so this is registration's project half, not the whole of registration.
    from calton.services.project_service import create_default_project_for

    create_default_project_for(session, user)
    return user
