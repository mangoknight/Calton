"""`GET /info` — the honest-downgrade endpoint.

Upstream uses /info's feature flags to tell clients what an instance can do, and
that is exactly the licence Calton needs to not implement the fork-only features
(admin panel, bot users, OAuth2 server, /ws, TimeEntry, plugins, all of /api/v2).
We report them false rather than pretending or 404ing.

⚠️ `concurrent_writes` must be **false**. Upstream computes it as
`DatabaseType != "sqlite"` (pkg/routes/api/shared/info.go:111) and Calton is on
SQLite, so overlapping write transactions deadlock. Clients read this to decide
whether to fire batched writes in parallel; reporting true would have them do
exactly the thing that breaks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

VERSION = "0.1.0-calton"

# Everything Calton does not implement (design §5.3). Listed explicitly rather
# than omitted: a missing key and a false one are different answers to a client.
DISABLED_FEATURES = {
    "caldav_enabled": False,
    "demo_mode_enabled": False,
    "email_reminders_enabled": False,
    "link_sharing_enabled": False,
    "public_teams_enabled": False,
    "totp_enabled": False,
    "user_deletion_enabled": False,
}

ENABLED_FEATURES = {
    "task_attachments_enabled": True,
    "task_comments_enabled": True,
    "allow_icon_changes": False,
}


def build_info(
    version: str = VERSION,
    frontend_url: str = "",
    motd: str = "",
    max_file_size: str = "20MB",
    max_items_per_page: int = 50,
    registration_enabled: bool = True,
    webhooks_enabled: bool = True,
) -> dict[str, Any]:
    """The /info body. Field names are a superset of upstream's."""
    return {
        "version": version,
        "frontend_url": frontend_url,
        "motd": motd,
        "max_file_size": max_file_size,
        "max_items_per_page": max_items_per_page,
        # Go is inconsistent between these three and we copy it exactly:
        #   available_migrators           — always a populated literal upstream
        #   enabled_background_providers  — nil slice when none, so JSON null
        #   enabled_pro_features          — [] (a made slice)
        # Measured against a running Go server, not inferred. Tidying any of them
        # into a uniform shape is a difference clients can branch on.
        "available_migrators": [],
        "enabled_background_providers": None,
        "enabled_pro_features": [],
        "legal": {"imprint_url": "", "privacy_policy_url": ""},
        "auth": {
            "local": {"enabled": True, "registration_enabled": registration_enabled},
            "ldap": {"enabled": False},
            "openid_connect": {"enabled": False, "providers": None},
        },
        # SQLite. See the module docstring — this one is not cosmetic.
        "concurrent_writes": False,
        **DISABLED_FEATURES,
        **ENABLED_FEATURES,
        # ⚠️ Derived, not a constant, and it is the only one of these that is. Upstream
        # reports it from `webhooks.enabled`, measured on both settings — false on the
        # parity harness's plane and true on upstream's defaults. A constant here would
        # be wrong on one of the two, and /info is compared on every parity case.
        "webhooks_enabled": webhooks_enabled,
    }


class LocalAuthInfo(BaseModel):
    enabled: bool = True
    # Nested here, NOT at the top level. Measured against the real Go server:
    # its /info has no top-level registration_enabled at all. Reading the swagger
    # alone gave the wrong shape; the parity harness caught it on its first run.
    registration_enabled: bool = True


class LdapAuthInfo(BaseModel):
    enabled: bool = False


class OpenIDAuthInfo(BaseModel):
    enabled: bool = False
    # null, not []. Go leaves the slice nil and encoding/json renders nil as null;
    # "tidying" it to an empty list is a difference clients can branch on.
    providers: list[dict[str, Any]] | None = None


class AuthInfo(BaseModel):
    local: LocalAuthInfo = Field(default_factory=LocalAuthInfo)
    ldap: LdapAuthInfo = Field(default_factory=LdapAuthInfo)
    openid_connect: OpenIDAuthInfo = Field(default_factory=OpenIDAuthInfo)


class LegalInfo(BaseModel):
    imprint_url: str = ""
    privacy_policy_url: str = ""


class CaltonInfo(BaseModel):
    """Declared so the contract diff has a schema to compare.

    A handler annotated ``-> dict`` produces an *empty* response schema in the
    generated OpenAPI, so the diff reports every upstream field as missing even
    though the runtime body is correct. Field names here are the contract; the
    values come from build_info().
    """

    # ⚠️ **The declaration order below IS the wire order, and it is upstream's, not
    # a tidy one.** `response_model=CaltonInfo` means FastAPI serialises through
    # this class, so Pydantic's field order — not `build_info()`'s dict — decides
    # what a client reads off the socket.
    #
    # It used to be grouped sensibly (scalars, then structures, then every
    # capability flag together, alphabetically). That grouping was the defect:
    # **17 of these 22 fields sat in a different position than upstream's**. Go's
    # order looks arbitrary because it is — it is `struct` declaration order in
    # `pkg/routes/api/shared/info.go`, where the flags were appended over time.
    # Measured against a running reference, field by field; do not re-tidy it.
    #
    # Why it matters even though every *value* here already matched: a client that
    # parses positionally, or byte-checksums a cached response, sees the order. Our
    # hard constraint is the bytes. (`normalize.EXEMPT_KEYS` deliberately does not
    # exempt position for the same reason — see the note there.)
    #
    # ☠ Do not "fix" this by reordering `build_info()`'s dict instead: that dict is
    # not what reaches the wire while `response_model=` is set, so the change would
    # look right, do nothing, and read as though the problem were handled.
    version: str
    frontend_url: str
    motd: str
    link_sharing_enabled: bool
    max_file_size: str
    max_items_per_page: int
    available_migrators: list[str]
    task_attachments_enabled: bool
    enabled_background_providers: list[str] | None
    totp_enabled: bool
    legal: LegalInfo
    caldav_enabled: bool
    auth: AuthInfo
    email_reminders_enabled: bool
    user_deletion_enabled: bool
    task_comments_enabled: bool
    demo_mode_enabled: bool
    webhooks_enabled: bool
    public_teams_enabled: bool
    allow_icon_changes: bool
    enabled_pro_features: list[str]
    concurrent_writes: bool


def build_router(settings: Any = None) -> APIRouter:
    router = APIRouter()

    # Cache-Control: no-store is applied API-wide by main.py's middleware, matching
    # the Go server which sets it on every routed /api/v1 response.
    @router.get("/info", response_model=CaltonInfo)
    def get_info() -> dict[str, Any]:
        if settings is None:
            return build_info()
        return build_info(
            frontend_url=settings.service.publicurl,
            max_file_size=settings.files.maxsize,
            max_items_per_page=settings.service.maxitemsperpage,
            motd=settings.service.motd,
            registration_enabled=settings.service.enableregistration,
            webhooks_enabled=settings.webhooks.enabled,
        )

    return router
