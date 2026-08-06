"""`GET /routes` — the registry, serialised.

The frontend reads this to build the API token permission picker, and T15's token
check reads the same RouteRegistry instance. One table, two consumers, so a
permission the UI offers is always a permission that authorises something.

Upstream's output is diffed byte-for-byte in CI (AC-2), which is why to_json()
sorts: dict ordering must not be the thing that makes the diff flap.
"""

from fastapi import APIRouter

from calton.auth.deps import CurrentSubject
from calton.core.route_registry import RouteRegistry
from calton.core.route_registry import registry as default_registry


def build_router(registry: RouteRegistry | None = None) -> APIRouter:
    router = APIRouter()
    table = registry if registry is not None else default_registry

    @router.get("/routes")
    def get_routes(subject: CurrentSubject) -> dict[str, dict[str, dict[str, str]]]:
        """Authenticated, deliberately.

        ⚠️ Serving this anonymously publishes the complete API-token permission
        table — every group and action an attacker could ask a phished user to
        grant, and a map of which routes exist. Measured: the reference server
        answers 401 to an anonymous request. An API token reaches it only with
        `other.routes` granted.
        """
        return table.to_json()

    return router
