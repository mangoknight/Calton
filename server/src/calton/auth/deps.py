"""The single authentication dependency every protected route hangs off.

``get_auth_subject`` resolves a request to a ``User`` or a ``LinkShare``. Phase 1
implements the JWT path; the ``tk_`` API-token path lands in T15 and link shares
in Phase 2, both by extending :func:`resolve_subject` rather than by adding a
second dependency — one entry point is what keeps the JWT-only gate (T15) from
being bypassable by an alternative route.

⚠️ Every failure here renders the middleware's 401, ``{"code": 11, ...}``, with
one message for missing, malformed and expired alike. That is measured behaviour
and also the reason not to "improve" it: a distinct message per cause tells an
attacker which of the three they achieved.

Note this is *not* "every 401 is code 11". A domain error that happens to carry
401 keeps its own code (``user.ErrInvalidUserContext`` is 1027), and the handler
401s on ``/user/token/refresh`` use the bare ``{"message": ...}`` shape with no
code at all — see ``api/v1/auth.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from calton.auth import api_token
from calton.auth import jwt as jwt_auth
from calton.config import Settings
from calton.core.errors import UnauthorizedError
from calton.core.route_registry import registry
from calton.db.session import get_db
from calton.models.user import User

BEARER_PREFIX = "Bearer "

#: Routes an API token may never reach, whatever it was granted.
#:
#: ⚠️ This is an explicit list because **it cannot be derived**. Three endpoints
#: refuse API tokens for three different reasons, and the measured matrix
#: (``go_api_tokens.json.acceptance_matrix``) is the only thing that separates
#: them:
#:
#: * ``/tokens`` — its group is ungrantable, so no token can ever hold it.
#:   Genuinely JWT-only, and load-bearing: otherwise one leaked token mints more.
#: * ``/user/logout`` — derives the group ``user_logout``, excluded by the
#:   ``user_`` prefix. Upstream still lists it in ``/routes`` as ``other.logout``,
#:   registered but ungrantable.
#: * ``/users`` — **not on this list.** It *is* reachable by a token granting
#:   ``other.users``. The design calls it JWT-only; measurement disagrees, and a
#:   probe using a token without the ``other`` group is what created that belief.
JWT_ONLY_PATHS = frozenset(
    {
        "/api/v1/tokens",
        "/api/v1/tokens/{tokenID}",
        "/api/v1/user/logout",
    }
)

#: Prefixes reserved for the same treatment. Neither area exists in Phase 1; they
#: are listed so that adding one cannot accidentally open it to API tokens.
JWT_ONLY_PREFIXES = ("/api/v1/user/settings/token/caldav", "/api/v1/admin/")

#: Routes any authenticated token may use without a permission check.
#:
#: ``/token/test`` answers 200 for every token in the matrix, including ones
#: granted a single unrelated subkey — it validates the credential rather than
#: authorising an action, so there is nothing to check it against.
TOKEN_UNCHECKED_PATHS = frozenset({"/api/v1/token/test"})


@dataclass(frozen=True)
class AuthSubject:
    """Who a request is acting as.

    ``session_id`` is present only for JWT-authenticated users; an API token has
    no session, which is why it is optional rather than an empty string.

    ``credential`` says **how** the caller authenticated, and one response field depends
    on it. Upstream builds a JWT subject from the token's claims — id and username, no
    timestamps — while an API token subject is loaded from the users table. Any response
    that embeds *the subject itself* rather than re-reading the row therefore carries the
    **zero time** under a JWT and the real values under a token. Measured 2x2 on the
    reference server (``harness/probe_coder_e_owner.py``):

        path                       jwt        api token
        GET /projects  ordinary    real       real        <- loads the users row
        GET /projects  pseudo      **ZERO**   real        <- embeds the subject
        GET /projects/950          real       real        <- loads the users row
        PUT /projects  (create)    **ZERO**   real        <- embeds the subject
        POST /projects/950         real       real        <- loads the users row

    ⚠️ Do not derive this from ``session_id``. It reads like the same question and is
    right in every case measured so far, but it is populated from an **optional** claim
    (``sid``): a JWT without one would be silently classified as an API token, and the
    only visible symptom would be two timestamps in one nested object.
    """

    user: User
    session_id: str | None = None
    #: Set explicitly at both construction sites below rather than defaulted, so a new
    #: authentication path has to state which kind it is instead of inheriting an answer.
    credential: Literal["jwt", "api_token"] = "api_token"

    @property
    def timestamps_are_zero(self) -> bool:
        """Whether embedding this subject in a response yields Go's zero time.

        Named for the observable consequence rather than for ``credential == "jwt"``:
        the callers care about what goes on the wire, and stating the rule once here
        keeps the three serialisation sites from each re-deriving it.
        """
        return self.credential == "jwt"

    @property
    def id(self) -> int:
        """The acting user's id.

        Routes that read the subject off ``request.state.auth`` ask for ``.id``
        rather than ``.user.id`` — that is the shape the resource lines were
        written against. Exposing it here keeps them from reaching through to the
        ORM object for the one field they actually want.
        """
        return int(self.user.id)


def bearer_token(request: Request) -> str:
    """The credential from the Authorization header.

    Raises rather than returning None: no header and a malformed header are the
    same answer to the client, so there is nothing for a caller to branch on.
    """
    header = request.headers.get("authorization", "")
    if not header.startswith(BEARER_PREFIX):
        # Includes the case of a valid token sent without the "Bearer " prefix,
        # which upstream also rejects.
        raise UnauthorizedError
    return header[len(BEARER_PREFIX) :].strip()


def resolve_subject(
    request: Request, db: DbSession, settings: Settings | None = None
) -> AuthSubject:
    """Turn a request's credential into a subject, or raise the 401.

    ``settings`` is resolved lazily, from ``app.state``, and only on the JWT
    branch. Reading it up front made an unauthenticated request to an app that
    had not set it 500 instead of 401 — the credential check has to come first,
    since that is the path an anonymous caller takes.
    """
    token = bearer_token(request)

    if api_token.looks_like_api_token(token):
        return _resolve_api_token(request, db, token)

    if settings is None:
        settings = request.app.state.settings

    try:
        claims = jwt_auth.decode(token, settings)
    except jwt_auth.InvalidTokenError as exc:
        raise UnauthorizedError from exc

    if claims["type"] != jwt_auth.TYPE_USER:
        # Link-share tokens authenticate, but nothing in Phase 1 accepts one.
        # Rejecting here rather than further in keeps a type-2 token from
        # reaching code that assumes a User.
        raise UnauthorizedError

    user = db.get(User, claims["id"])
    if user is None:
        # A token signed for a user who has since been deleted.
        raise UnauthorizedError

    return AuthSubject(user=user, session_id=claims.get("sid"), credential="jwt")


def route_template(request: Request) -> str | None:
    """The full route template for this request, e.g. ``/api/v1/tokens/{tokenID}``.

    ⚠️ Not ``request.url.path``. A concrete URL turns path parameters into static
    segments, so ``/api/v1/labels/5`` derives the group ``labels_5``, which is
    registered nowhere and refuses every call — fail-closed, but with a symptom
    that points nowhere near the cause. Loosening the match to compensate is how
    GHSA-v479 was introduced.

    ⚠️ Nor is it ``route.path_format`` on its own. ``scope["route"]`` is the route
    object from the *included* router, whose ``path_format`` is relative to the
    include prefix — ``/users``, not ``/api/v1/users``. Registrations use full
    paths, so the bare value misses every lookup and makes API tokens look
    universally broken. Same underlying quirk as the documented one about
    ``_IncludedRouter`` entries lacking ``.path``.

    The prefix is recovered by substituting the matched parameters back into the
    template and stripping that suffix from the URL — which keeps the ``{...}``
    placeholders intact, so nothing here can turn a parameter into a static
    segment.
    """
    route = request.scope.get("route")
    path_format: str | None = getattr(route, "path_format", None)
    if path_format is None:
        return None

    concrete = path_format
    for name, value in request.path_params.items():
        concrete = concrete.replace(f"{{{name}}}", str(value))

    url_path = request.url.path
    if concrete and url_path.endswith(concrete):
        return url_path[: len(url_path) - len(concrete)] + path_format
    return path_format


def _resolve_api_token(request: Request, db: DbSession, plaintext: str) -> AuthSubject:
    """Authenticate a ``tk_`` credential and check it may be used on this route.

    ⚠️ **Every refusal here is the 401 invalid-token body, never a 403.** That
    includes "this token is valid but was not granted this route", which reads
    like a permission problem and is therefore the one most likely to be
    implemented as 403. Upstream routes it to the same invalid-token exit, and
    the corpus pins it.
    """
    token = api_token.verify(db, plaintext)
    if token is None:
        raise UnauthorizedError

    path_format = route_template(request)
    if path_format is None:
        raise UnauthorizedError

    if path_format in JWT_ONLY_PATHS or path_format.startswith(JWT_ONLY_PREFIXES):
        raise UnauthorizedError

    if path_format in TOKEN_UNCHECKED_PATHS:
        return _subject_for(db, token.owner_id)

    resolved = registry.lookup(request.method, path_format)
    if resolved is None:
        # Unregistered routes are refused rather than allowed. The default has to
        # be closed: a route nobody registered is otherwise reachable by every
        # token ever issued.
        raise UnauthorizedError

    group, action = resolved
    if not api_token.authorises(token, group, action):
        raise UnauthorizedError

    return _subject_for(db, token.owner_id)


def _subject_for(db: DbSession, owner_id: int) -> AuthSubject:
    owner = db.get(User, owner_id)
    if owner is None:
        raise UnauthorizedError

    # No session_id: an API token is not tied to a login session, which is why
    # logging out does not revoke one.
    return AuthSubject(user=owner, credential="api_token")


def get_auth_subject(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
) -> AuthSubject:
    """Resolve the caller, and publish the result on ``request.state.auth``.

    ⚠️ The assignment is the wiring, not a convenience. The resource lines do not
    take the subject as a handler argument — they read ``request.state.auth`` (see
    ``api/v1/tasks.py::_auth_user_id``). Nothing populated it, so every task route
    answered 401 to a valid JWT while both lines' own unit tests stayed green: the
    tasks tests supply the subject from a stub middleware, and the auth tests only
    ever exercise routes that take ``CurrentSubject`` as an argument.

    ⚠️ This has to be a **dependency, not an HTTP middleware**, even though the
    docstrings the resource lines were written against say "middleware". The API
    token check calls :func:`route_template`, which reads ``scope["route"]``, and
    Starlette only sets that during routing — after every ``@app.middleware("http")``
    has already run its pre-``call_next`` half. Resolving there would make
    ``route_template`` return None and refuse every API token.
    """
    subject = resolve_subject(request, db)
    request.state.auth = subject
    return subject


#: The annotation routes use, so no handler repeats the Depends() wiring.
CurrentSubject = Annotated[AuthSubject, Depends(get_auth_subject)]


def auth_user_id(request: Request) -> int:
    """The acting user's id, off ``request.state.auth``.

    The resource routers read the subject from the request rather than taking it as a
    handler argument, so that the CRUD base — one signature shared by 59 endpoints — does
    not have to carry it. This is that read, in one place, for routers written from here
    on. ``api/v1/tasks.py`` keeps its own copy deliberately: it is load-bearing and
    already reviewed, and churning it buys nothing.

    Raises rather than defaulting. A silent fallback to some user id would make every
    endpoint using it publicly writable as that user, which is the failure mode worth
    being loud about.
    """
    subject = getattr(request.state, "auth", None)
    user_id = getattr(subject, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError
    return user_id
