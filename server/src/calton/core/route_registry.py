"""route_registry — (group, action) keys for `GET /routes` and API token checks.

A faithful port of pkg/models/api_routes.go. The same table serves two callers,
which is the whole point: `GET /routes` tells the frontend which permissions it
may offer, and the API token middleware decides authorisation from it. If they
came from separate tables they would drift, and a token would be granted a
permission that authorises nothing (or worse, the reverse).

Getting a group name wrong is not a subtle bug — every MCP call against that
resource returns 403.

⚠️ The design note "group = first path segment" is wrong. Upstream joins *all*
non-parameter segments with `_`, so `/projects/:project/views` is the group
`projects_views`, not `projects`. Three names are then special-cased back
(getRouteGroupName, api_routes.go:96-104).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from calton.core.crud_router import Action, action_for

# api_routes.go:179-211. Membership decides whether a route files under its own
# group as a CRUD action, or under a parent group as a named sub-key.
CRUD_RESOURCES = frozenset(
    {
        "projects",
        "tasks",
        "teams",
        "labels",
        "filters",
        "notifications",
        "webhooks",
        "reactions",
        "shares",
        "buckets",
        "views",
        "assignees",
        "comments",
        "relations",
        "attachments",
        "time_entries",
        "projects_views",
        "projects_teams",
        "projects_users",
        "projects_shares",
        "projects_webhooks",
        "projects_buckets",
        "tasks_attachments",
        "tasks_assignees",
        "tasks_labels",
        "tasks_comments",
        "tasks_relations",
        "teams_members",
        "projects_views_tasks",
    }
)

# api_routes.go:96-104. `tasks_all` is already here upstream, so Calton's
# /tasks/all alias (T34) needs no extra rule to land on ("tasks", "read_all").
GROUP_RENAMES: dict[str, tuple[str, list[str]]] = {
    "projects_tasks": ("tasks", ["tasks"]),
    "tasks_all": ("tasks", ["tasks"]),
    "projects_tasks_bulk": ("tasks_bulk", ["tasks_bulk"]),
}

# api_routes.go:252-259. Never grantable to an API token, so they must never
# appear in the registry. T15 rejects any route missing from it, which is what
# stops a leaked read-only token from enumerating users or minting more tokens.
EXCLUDED_GROUPS = frozenset({"token_test", "subscriptions", "tokens", "*", "oauth_authorize"})
EXCLUDED_GROUP_PREFIXES = ("user_",)

# api_routes.go:38-52 seeds these before any route is registered.
SEED_ROUTES: dict[str, dict[str, dict[str, str]]] = {
    "caldav": {"access": {"path": "/dav/*", "method": "ANY"}},
    "feeds": {"access": {"path": "/feeds/*", "method": "GET"}},
}


@dataclass(frozen=True)
class Collision:
    """Which route ``GET /routes`` publishes when several land on one key.

    ``method``/``path`` name the winner **as FastAPI registered it**; every other
    route on the key is still authorised, because authorisation reads
    ``_registered`` and not this.
    """

    method: str
    path: str
    owner: str
    why: str


#: The (group, action) keys more than one route legitimately claims, and which
#: route's detail is published for each.
#:
#: Without this the last registration silently won, and "silently" is the whole
#: problem: `tasks.read_all` published `/api/v1/projects/:project/tasks` where
#: upstream publishes `/api/v1/tasks`, purely because of the order two routers
#: happened to be included. Nothing failed, and the frontend's permission picker
#: renders whatever string it is handed.
#:
#: An undeclared collision now raises at app build time. That is the registry
#: version of "存在 ≠ 生效": a second route claiming an existing key is either a
#: deliberate alias, which belongs here, or a real mistake that used to be
#: invisible.
COLLISIONS: dict[tuple[str, str], Collision] = {
    ("tasks", "read_all"): Collision(
        method="GET",
        path="/api/v1/tasks",
        owner="—  (upstream has this same collision)",
        why=(
            "Upstream registers /api/v1/tasks, /api/v1/tasks/all and "
            "/api/v1/projects/:project/tasks onto one key too, and publishes the "
            "bare /api/v1/tasks — measured against the reference server. The "
            "alias is required by AC-3 (calton-mcp@1.0.4 calls /tasks/all with a "
            "token granted tasks.read_all), so all three stay authorised."
        ),
    ),
}


#: Collisions that exist only because Calton serves a verb upstream does not.
#: Kept separate from `COLLISIONS` on purpose: those are aliases we intend to
#: have, these are damage.
#:
#: ⚠️ The published detail here is deliberately the **wrong** one. Upstream
#: answers 405 to PATCH on these paths, so it has no such collision at all, and
#: `routes.ok` reporting `update.method: go='POST' calton='PATCH'` is currently
#: the *only* thing that reports the extra verb — nothing else asserts it.
#: Publishing POST would make `routes.ok` greener while leaving the PATCH routes
#: answering 200 where upstream 405s, which is the exact trade this project keeps
#: refusing: a check that goes quiet without the defect going away.
#: Routes where upstream's ``/routes`` parameter *names* differ from its swagger's.
#:
#: ⚠️ These are two different upstream surfaces and they genuinely disagree, so
#: there is no single spelling that satisfies both:
#:
#:     swagger      /tasks/{id}                   /projects/{project}/views/{id}
#:     GET /routes  /tasks/:projecttask           /projects/:project/views/:view
#:
#: `/routes` publishes Echo's route pattern, whose parameter names come from the
#: route registration; the swagger names are hand-written annotations. The
#: contract diff compares against swagger and `routes.ok` compares against
#: `/routes`, so **renaming the FastAPI path parameters cannot fix this** — it
#: just moves the red from one check to the other. The route templates therefore
#: keep swagger's names (see the note in api/v1/views.py, which is right), and
#: only this serialisation carries Echo's.
#:
#: Derived by diffing our registry against a running reference server, not typed
#: from the swagger file. `test_route_registry` asserts every entry still matches
#: a registered route, so an entry cannot outlive the route it renames.
ECHO_PATH_OVERRIDES: dict[str, str] = {
    "/api/v1/projects/{project}/views/{id}": "/api/v1/projects/:project/views/:view",
    "/api/v1/tasks/{task}": "/api/v1/tasks/:projecttask",
    "/api/v1/tasks/{task}/assignees": "/api/v1/tasks/:projecttask/assignees",
    "/api/v1/tasks/{task}/assignees/bulk": "/api/v1/tasks/:projecttask/assignees/bulk",
    "/api/v1/tasks/{task}/assignees/{userID}": "/api/v1/tasks/:projecttask/assignees/:user",
    "/api/v1/tasks/{task}/labels": "/api/v1/tasks/:projecttask/labels",
    "/api/v1/tasks/{task}/labels/bulk": "/api/v1/tasks/:projecttask/labels/bulk",
    "/api/v1/tasks/{task}/labels/{label}": "/api/v1/tasks/:projecttask/labels/:label",
}


def echo_path(path: str) -> str:
    """``/labels/{label}`` -> ``/labels/:label``, the syntax upstream publishes.

    Only the **serialised** form changes. Paths are stored as FastAPI registered
    them, because that is the spelling `_registered` is keyed by and the spelling
    ``auth/deps.py`` hands to :meth:`RouteRegistry.lookup` (it reads
    ``route.path_format``). Rewriting at registration time would leave every API
    token lookup missing its key, and a miss here is fail-closed — the symptom is
    every authorised call 403ing, with nothing pointing at the cause.

    Measured: upstream answers ``{"path": "/api/v1/labels/:label", ...}``. Echo is
    what produced it, and the frontend's permission picker renders the string it
    is given, so the syntax is part of the wire contract rather than cosmetic.
    """
    override = ECHO_PATH_OVERRIDES.get(path)
    if override is not None:
        return override
    return "/".join(
        f":{segment[1:-1]}" if segment.startswith("{") and segment.endswith("}") else segment
        for segment in path.split("/")
    )


@dataclass(frozen=True)
class RouteDetail:
    path: str
    method: str

    def to_json(self) -> dict[str, str]:
        return {"path": echo_path(self.path), "method": self.method}


def strip_api_version(path: str) -> str:
    for prefix in ("/api/v1/", "/api/v2/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def canonical_group(group: str) -> str:
    """Hyphens become underscores; the frontend snake_cases payloads and a
    hyphenated slug cannot round-trip through it (api_routes.go:78-81)."""
    return group.replace("-", "_")


def group_name_of(path: str) -> tuple[str, list[str]]:
    """(group, parts) for a path, copying getRouteGroupName.

    Parameter segments — ``:param`` or ``{param}`` — are dropped, then the rest
    are joined with underscores.
    """
    parts = [
        canonical_group(part)
        for part in strip_api_version(path).split("/")
        if part and not part.startswith(":") and not (part.startswith("{") and part.endswith("}"))
    ]
    name = "_".join(parts)
    if name in GROUP_RENAMES:
        renamed, renamed_parts = GROUP_RENAMES[name]
        return renamed, list(renamed_parts)
    return name, parts


def ends_with_param(path: str) -> bool:
    last = path.rstrip("/").split("/")[-1]
    return last.startswith(":") or (last.startswith("{") and last.endswith("}"))


def is_standard_crud_route(group: str, parts: list[str]) -> bool:
    """api_routes.go:179-234."""
    if group in CRUD_RESOURCES:
        return True
    if group.endswith("_bulk") and group.removesuffix("_bulk") in CRUD_RESOURCES:
        return True
    return len(parts) == 1 and parts[0] in CRUD_RESOURCES


class RouteRegistry:
    """Accumulates (group -> action -> detail) as routes are registered."""

    def __init__(self, seed: bool = True) -> None:
        self.routes: dict[str, dict[str, RouteDetail]] = {}
        # (method, path) -> (group, action) for every route actually registered.
        # lookup() consults this for non-CRUD routes, whose keys cannot be
        # re-derived from the path alone: _register_non_crud() disambiguates
        # collisions by appending the method, and nothing in the path records
        # that it did. Deriving instead of remembering would silently authorise
        # the wrong key for whichever of the colliding routes lost the race.
        self._registered: dict[tuple[str, str], tuple[str, str]] = {}
        if seed:
            for group, entries in SEED_ROUTES.items():
                self.routes[group] = {
                    action: RouteDetail(**detail) for action, detail in entries.items()
                }

    def _group(self, name: str) -> dict[str, RouteDetail]:
        return self.routes.setdefault(name, {})

    def _publish(self, group: str, action: str, detail: RouteDetail) -> None:
        """Store the detail `GET /routes` will show for (group, action).

        The only place `self.routes` is written. A second route arriving on a key
        used to overwrite the first without a word, which made the published path
        a function of router include order — see `COLLISIONS`.
        """
        entries = self._group(group)
        existing = entries.get(action)
        if existing is None:
            entries[action] = detail
            return
        if existing.path == detail.path and existing.method == detail.method:
            return

        declared = COLLISIONS.get((group, action))
        if declared is None:
            raise RuntimeError(
                f"two routes claim ({group!r}, {action!r}) and neither is declared:\n"
                f"    {existing.method} {existing.path}\n"
                f"    {detail.method} {detail.path}\n"
                f"Whichever registered last would silently become the one "
                f"`GET /routes` publishes, and the order is an accident of how the "
                f"routers are included. If this is a deliberate alias, add it to "
                f"COLLISIONS in {__name__} naming the path to publish; if it is "
                f"not, one of these two routes should not exist."
            )
        entries[action] = RouteDetail(path=declared.path, method=declared.method)

    def paths(self) -> list[tuple[str, str]]:
        """Every ``(method, path)`` that has been registered.

        Exposed for the wiring test, which walks what ``create_app`` actually
        mounted rather than a list someone has to remember to update.
        """
        return list(self._registered)

    def register(self, method: str, path: str, requires_jwt: bool = True) -> None:
        """Record one route. Mirrors CollectRoutesForAPITokenUsage.

        Routes that do not require JWT are not token-grantable and are skipped,
        as are the excluded groups.
        """
        if not requires_jwt:
            return

        method = method.upper()

        # Registering the same route twice must be a no-op. The collision handling
        # below exists for two *different* routes landing on one key and appends
        # the method to disambiguate; letting a repeat reach it turns "users" into
        # "users_get" on the second pass, so every token granted "other.users"
        # stops working. Building two apps in one process is enough to trigger it,
        # which is exactly what a test session does.
        if (method, path) in self._registered:
            return

        group, parts = group_name_of(path)

        if group in EXCLUDED_GROUPS or group.startswith(EXCLUDED_GROUP_PREFIXES):
            return

        detail = RouteDetail(path=path, method=method)
        is_attachments = group == "tasks_attachments"

        if not is_standard_crud_route(group, parts) and not is_attachments:
            self._register_non_crud(group, parts, method, detail)
            return

        if group.endswith("_bulk"):
            parent = group.removesuffix("_bulk")
            action = action_for(method, ends_with_param(path))
            self._publish(parent, f"{action}_bulk", detail)
            self._registered[method, path] = (parent, f"{action}_bulk")
            return

        action = action_for(method, ends_with_param(path))
        self._publish(group, str(action), detail)
        self._registered[method, path] = (group, str(action))

        if is_attachments:
            # Attachments use custom handlers rather than WebHandler, so the
            # generic mapping misses upload and download (api_routes.go:344-357).
            if method == "PUT":
                self._publish(group, str(Action.CREATE), detail)
            if method == "GET" and ends_with_param(path):
                self._publish(group, str(Action.READ_ONE), detail)

    def _register_non_crud(
        self, group: str, parts: list[str], method: str, detail: RouteDetail
    ) -> None:
        """Non-CRUD routes file under a parent group, or under "other"."""
        if len(parts) == 1:
            # api_routes.go:292-296. Unreachable upstream — this arm only runs
            # for non-CRUD routes and "notifications" is in crudResources, so
            # POST /notifications files as a plain "update". Kept for fidelity in
            # case the resource ever leaves that set.
            if group == "notifications" and method == "POST":
                self._publish("notifications", "mark_all_as_read", detail)
                self._registered[method, detail.path] = ("notifications", "mark_all_as_read")
                return
            other = self._group("other")
            key = group if group not in other else f"{group}_{method.lower()}"
            other[key] = detail
            self._registered[method, detail.path] = ("other", key)
            return

        parent = parts[0]
        subkey = "_".join(parts[1:])
        entries = self._group(parent)
        if subkey in entries:
            subkey = f"{subkey}_{method.lower()}"
        entries[subkey] = detail
        self._registered[method, detail.path] = (parent, subkey)

    def register_crud_router(self, crud: Any, prefix: str = "/api/v1") -> None:
        """Register everything a CRUDRouter mounted, so the two cannot disagree."""
        for method, path, _action in crud.registered_actions():
            self.register(method, f"{prefix}{path}")

    # Deliberately more permissive than upstream in one respect: when several
    # paths collapse onto the same (group, action) — /tasks and /tasks/all both
    # being ("tasks", "read_all") — Go only authorises the single path stored in
    # its RouteDetail, whereas we authorise the pair. That is a conscious choice,
    # not an oversight: AC-3 requires calton-mcp@1.0.4 to reach /tasks/all with a
    # token granted tasks.read_all, and the alias exists for exactly that. The
    # widening is confined to paths that already share a permission key.
    def can(self, group: str, action: str) -> bool:
        """Whether a token holding (group, action) authorises anything.

        A route absent from the registry is refused rather than allowed — the
        default has to be closed, or an unregistered route is wide open.
        """
        return action in self.routes.get(group, {})

    def lookup(self, method: str, path: str) -> tuple[str, str] | None:
        """The (group, action) a request maps to, or None if unregistered.

        ⚠️ ``path`` must be the **route template** — ``/api/v1/labels/{label}`` or
        ``/api/v1/labels/:label`` — never a concrete URL. Feeding it
        ``/api/v1/labels/5`` makes the group ``labels_5``, which resolves to None
        and refuses the request: fail-closed, but with a symptom (every call 403s)
        that points nowhere near the cause. When wiring this into the API token
        check, take FastAPI's ``request.scope["route"].path_format``, which is the
        equivalent of Echo's ``c.Path()``.

        Do not "fix" a miss here by loosening the match to prefixes or by
        stripping trailing segments. That is precisely the shape of GHSA-v479,
        where a permissive path match let a token reach routes it was never
        granted. If a lookup misses, the caller passed the wrong thing.
        """
        group, parts = group_name_of(path)
        if group in EXCLUDED_GROUPS or group.startswith(EXCLUDED_GROUP_PREFIXES):
            return None
        if not is_standard_crud_route(group, parts):
            # Non-CRUD routes file under "other" or under a parent group, and
            # their keys are not recoverable from the path. Resolve them only if
            # they were actually registered — measured behaviour: a token
            # granting other.user reaches GET /user, other.users reaches
            # GET /users, and neither reaches the other.
            #
            # ⚠️ Requiring a registration is what keeps this from reopening
            # GHSA-v479. A concrete URL such as /api/v1/labels/5 derives the
            # group "labels_5", which was never registered, so it still resolves
            # to None rather than to ("labels", "5").
            return self._registered.get((method.upper(), path))
        if group.endswith("_bulk"):
            parent = group.removesuffix("_bulk")
            return parent, f"{action_for(method, ends_with_param(path))}_bulk"
        return group, str(action_for(method, ends_with_param(path)))

    def to_json(self) -> dict[str, dict[str, dict[str, str]]]:
        """The body of `GET /routes`."""
        return {
            group: {action: detail.to_json() for action, detail in sorted(entries.items())}
            for group, entries in sorted(self.routes.items())
        }


registry = RouteRegistry()
