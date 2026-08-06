"""Load the golden Calton contract and reduce it to what we actually diff on.

The golden file is Swagger 2.0; FastAPI emits OpenAPI 3.1. Rather than convert
one into the other, both are reduced to the same small shape — for each
operation, the set of response field names, each field's *canonical type*, and
the set of required parameter names.

Types were originally left out, on the stated grounds that "types are a much
weaker signal and differ harmlessly between the two spec versions". The first
half of that turned out to be wrong: a widened type is exactly the kind of break
that reaches a client silently — a wrong *value* makes a client throw, a wrong
*type* makes it accept the wrong thing, and the generated TS types are built from
this file. The concrete case that prompted the change was a `number` field that
started advertising `anyOf: [integer, number]`; nothing in the suite noticed.

The second half was right, and is why this compares a **canonical** type rather
than the raw schema. `format` (int64/double), `title`, `default` and the two
spellings of nullability are spec-generator dialect, and comparing them would
bury the real differences under noise — the same "noise drowns signal" failure
that makes a desynchronised diff worthless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contract"
GOLDEN_PATH = CONTRACT_DIR / "calton-v1-swagger.json"
CORRECTIONS_PATH = CONTRACT_DIR / "swagger-corrections.yaml"
WHITELIST_PATH = CONTRACT_DIR / "phase1-endpoints.yaml"
ALIASES_PATH = CONTRACT_DIR / "aliases.yaml"

#: The corrections applied and written out, for consumers that cannot run our
#: Python — notably the frontend's openapi-typescript generation. Built by
#: scripts/build_corrected_contract.py; tests keep it from drifting.
CORRECTED_PATH = CONTRACT_DIR / "calton-v1-corrected.json"

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")

OperationKey = tuple[str, str]
"""(METHOD, normalised path), e.g. ("GET", "/projects/{}")."""


PARAM_RE = re.compile(r"\{[^}]*\}|:[^/]+")


def normalise_path(path: str) -> str:
    """Replace every path parameter with a single placeholder.

    Parameter *names* are not part of the wire contract, and upstream is wildly
    inconsistent about them — the same /projects/{...} appears as {id}, {project}
    and {projectID} across the swagger, while the route table spells them
    :project, :label and so on. Without this, correctly implementing
    /labels/{label} would not match the whitelist's /labels/{id}: the diff would
    report missing_operation and the live test would skip it forever, satisfying
    AC-2 by doing nothing at all.
    """
    return PARAM_RE.sub("{}", path)


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    response_fields: frozenset[str] = field(default_factory=frozenset)
    """Field names of the success response body. Empty for a non-object body."""

    required_params: frozenset[str] = field(default_factory=frozenset)
    """Names of parameters the spec marks required."""

    field_types: frozenset[tuple[str, frozenset[str]]] = field(default_factory=frozenset)
    """``(field name, canonical types)`` for each response field.

    A set of pairs rather than a mapping so ``Operation`` stays hashable, which
    the frozen dataclass and the existing frozenset fields both assume. Use
    :attr:`types_by_field` to read it.

    The value is a *set* of types because a field reached through ``oneOf``/
    ``anyOf`` can legitimately have a different type per branch, exactly as
    :func:`_field_names` unions the branches' names.
    """

    @property
    def key(self) -> OperationKey:
        return (self.method, self.path)

    @property
    def types_by_field(self) -> dict[str, frozenset[str]]:
        return dict(self.field_types)


def _resolve(
    schema: dict[str, Any], spec: dict[str, Any], seen: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Follow a $ref. Cycles resolve to an empty schema rather than recursing forever."""
    ref = schema.get("$ref")
    if not ref:
        return schema
    if ref in seen:
        return {}
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    if not isinstance(node, dict):
        return {}
    resolved: dict[str, Any] = _resolve(node, spec, seen | {ref})
    return resolved


def _field_names(schema: dict[str, Any], spec: dict[str, Any]) -> frozenset[str]:
    """Object property names, looking through $ref, arrays, allOf and oneOf/anyOf.

    ``oneOf``/``anyOf`` branches are **unioned**, not intersected, and that choice
    needs stating because it deliberately weakens the check.

    A polymorphic response is one where the branches are different types by
    design. `GET /projects/{id}/views/{view}/tasks` returns `Bucket[]` when the
    view has a bucket configuration and `Task[]` otherwise
    (``task_collection.go:173-184``) — so intersecting the branches would demand
    that Bucket carry every Task field, and the diff would be permanently and
    unfixably red. Union means "some branch provides this field", which is the
    strongest claim that is true of a union type.

    What it still catches: dropping a field from *every* branch, which is what an
    actual regression looks like. What it cannot catch: a field present in one
    branch and missing from another. That blind spot is inherent — upstream's own
    swagger declares only `Task[]` for that path and never describes the
    polymorphism, so there is no contract to check the second branch against.

    Without this, FastAPI's `anyOf` for a Union response model matches no branch
    here and the operation reports **zero** fields, i.e. "missing every field
    upstream declares" — a loud failure, but a wrong one.
    """
    schema = _resolve(schema, spec)
    if not schema:
        return frozenset()
    if "items" in schema:
        return _field_names(schema["items"], spec)

    names: set[str] = set(schema.get("properties", {}))
    for branch in schema.get("allOf", []):
        names |= _field_names(branch, spec)

    # A polymorphic response — `GET /projects/{p}/views/{v}/tasks` answers either Task[]
    # or Bucket[] — reaches here as oneOf/anyOf. Without this the schema contributes *no*
    # field names, so the superset check compares against the empty set and passes
    # vacuously: exactly the failure the C-1 convention exists to prevent, and it would
    # have applied to the one endpoint in Phase 1 whose body is hardest to get right.
    #
    # The union is the right reading of "which fields may this response carry", but note
    # it is weaker than the monomorphic case: a field dropped from one branch can be
    # masked by another branch that happens to declare it. Where that matters, assert the
    # branch directly — see `test_the_task_branch_alone_satisfies_the_upstream_contract`.
    for branch in (*schema.get("oneOf", []), *schema.get("anyOf", [])):
        names |= _field_names(branch, spec)

    return frozenset(names)


def _canonical_types(schema: dict[str, Any], spec: dict[str, Any]) -> frozenset[str]:
    """The JSON types a value may take, reduced to what a client can break on.

    Kept deliberately coarse. What survives:

    * the base JSON type — ``string``/``number``/``integer``/``boolean``/``object``/
      ``array``. ``integer`` and ``number`` stay **distinct**: upstream declares
      ``number`` for a Go ``float64``, and advertising ``integer`` there is what a
      generated client would type wrongly.
    * an array's element type, as ``array<...>``, since ``string[]`` and
      ``object[]`` are different contracts.

    What is dropped, because it is generator dialect rather than contract:
    ``format`` (``int64``, ``double``, ``date-time``), ``title``, ``default``,
    ``description``, and every validation keyword. Comparing those would report a
    difference on nearly every field and bury the handful that matter.

    **Nullability is dropped too, and that one needs justifying.** The golden file
    contains zero ``x-nullable`` and zero ``nullable`` keys — swaggo cannot emit
    them — so upstream's spec makes *no claim* about null either way. Comparing
    our ``X | None`` against that silence is not comparing two contracts; it
    reported 24 of the 26 differences on first run, and "fixing" them would have
    meant deleting ``| None`` from fields where **null is the measured upstream
    behaviour** (`created_by` on a partial update, `reminders`, a project's
    `views` in the collection). That is the failure mode where a gate is satisfied
    by making the implementation wrong.

    Nullability is a claim about what the server *sends*, so the instrument for it
    is the parity corpus against a running server, which already compares ``null``
    against a value. A spec that cannot express it is the wrong place to check it.

    An empty result means "no type declared", which both generators do for a bare
    ``{}`` schema; the diff treats that as "nothing to check" rather than as a
    mismatch, so an undeclared upstream type cannot manufacture a failure.
    """
    schema = _resolve(schema, spec)
    if not schema:
        return frozenset()

    types: set[str] = set()

    declared = schema.get("type")
    if isinstance(declared, str):
        types.add(declared)
    elif isinstance(declared, list):
        types.update(t for t in declared if isinstance(t, str))

    # See the docstring: upstream's spec cannot express nullability, so ours is
    # compared against silence rather than against a claim.
    types.discard("null")

    if "array" in types:
        items = schema.get("items")
        inner = _canonical_types(items, spec) if isinstance(items, dict) else frozenset()
        types.discard("array")
        types.add(f"array<{'|'.join(sorted(inner)) or '?'}>")

    for branch in (*schema.get("allOf", []), *schema.get("oneOf", []), *schema.get("anyOf", [])):
        if isinstance(branch, dict):
            types |= _canonical_types(branch, spec)

    return frozenset(types)


def _field_types(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, frozenset[str]]:
    """Canonical type per response field, mirroring :func:`_field_names`'s traversal.

    The two walk the same structure for the same reason, and a field that
    :func:`_field_names` reports must have an entry here or the diff would compare
    a name against a type it never collected.
    """
    schema = _resolve(schema, spec)
    if not schema:
        return {}
    if "items" in schema and "properties" not in schema:
        return _field_types(schema["items"], spec)

    collected: dict[str, frozenset[str]] = {}
    for name, prop in schema.get("properties", {}).items():
        if isinstance(prop, dict):
            collected[name] = _canonical_types(prop, spec)

    # Branches are unioned, matching _field_names: a field appearing in more than
    # one branch may legitimately differ in type between them.
    for branch in (*schema.get("allOf", []), *schema.get("oneOf", []), *schema.get("anyOf", [])):
        if not isinstance(branch, dict):
            continue
        for name, types in _field_types(branch, spec).items():
            collected[name] = collected.get(name, frozenset()) | types

    return collected


def _success_response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses", {})
    for status in sorted(responses):
        if not status.startswith("2"):
            continue
        body = responses[status]
        if "schema" in body:  # Swagger 2.0
            schema: dict[str, Any] = body["schema"]
            return schema
        content = body.get("content", {})  # OpenAPI 3.x
        for media_type, media in content.items():
            if "json" in media_type and "schema" in media:
                media_schema: dict[str, Any] = media["schema"]
                return media_schema
    return {}


def _required_params(operation: dict[str, Any], shared: list[dict[str, Any]]) -> frozenset[str]:
    """Required parameters, **excluding path parameters**.

    ⚠️ ``in: path`` is skipped deliberately, on both sides of the diff. OpenAPI requires
    every path parameter to be ``required: true`` (§4.8.12.1), so a path parameter cannot
    meaningfully differ in requiredness — but upstream's swagger is generated by swaggo,
    which omits the flag. Comparing it therefore reports "Calton requires ``task``, which
    upstream leaves optional" for any operation that declares its path parameters at all,
    which is a difference in how two generators spell an invariant, not in behaviour.

    That false positive has a cost beyond the noise: the way to silence it is to stop
    declaring path parameters and read them from ``request.path_params`` instead, and an
    operation with no ``parameters`` block is one schemathesis refuses to fuzz at all
    (``InvalidSchema: Path parameter 'task' is not defined``). So the check was pushing
    the code towards a shape that disables a different gate.

    Query and header parameters are still compared: those are genuinely optional-or-not,
    and requiring one upstream leaves optional is a real incompatibility.
    """
    params = list(shared) + list(operation.get("parameters", []))
    return frozenset(
        p["name"] for p in params if p.get("required") and "name" in p and p.get("in") != "path"
    )


def _operations_of(spec: dict[str, Any]) -> dict[OperationKey, Operation]:
    operations: dict[OperationKey, Operation] = {}
    for path, path_item in spec.get("paths", {}).items():
        shared = path_item.get("parameters", [])
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            body = _success_response_schema(operation)
            operations[(method.upper(), normalise_path(path))] = Operation(
                method=method.upper(),
                path=normalise_path(path),
                response_fields=_field_names(body, spec),
                required_params=_required_params(operation, shared),
                field_types=frozenset(_field_types(body, spec).items()),
            )
    return operations


def load_golden(apply_corrections: bool = True) -> dict[OperationKey, Operation]:
    """The frozen upstream contract, with the routes.go divergences applied.

    Pass ``apply_corrections=False`` to see the raw swagger — useful only for
    asserting that the corrections are still needed.
    """
    import json

    spec = json.loads(GOLDEN_PATH.read_text())
    operations = _operations_of(spec)
    if apply_corrections:
        operations = _apply_corrections(operations)
    return operations


def _apply_corrections(operations: dict[OperationKey, Operation]) -> dict[OperationKey, Operation]:
    corrections = yaml.safe_load(CORRECTIONS_PATH.read_text()) or {}

    for entry in corrections.get("replace", []):
        old = (entry["from"]["method"].upper(), normalise_path(entry["from"]["path"]))
        new = (entry["to"]["method"].upper(), normalise_path(entry["to"]["path"]))
        existing = operations.pop(old, None)
        if existing is None:
            raise ValueError(f"correction targets {old}, which is not in the golden contract")
        operations[new] = Operation(
            method=new[0],
            path=new[1],
            response_fields=existing.response_fields,
            required_params=existing.required_params,
        )

    for entry in corrections.get("response_fields_not_returned", []):
        key = (entry["method"].upper(), normalise_path(entry["path"]))
        existing = operations.pop(key, None)
        if existing is None:
            raise ValueError(f"correction targets {key}, which is not in the golden contract")
        dropped = frozenset(entry["fields"])
        unknown = dropped - existing.response_fields
        if unknown:
            # A field the golden never declared means the correction is stale.
            # Ignoring it silently would leave the entry sitting there forever
            # claiming to fix something that is no longer there.
            raise ValueError(f"correction for {key} drops undeclared fields: {sorted(unknown)}")
        operations[key] = Operation(
            method=key[0],
            path=key[1],
            response_fields=existing.response_fields - dropped,
            required_params=existing.required_params,
        )

    for entry in corrections.get("no_documented_body", []):
        key = (entry["method"].upper(), normalise_path(entry["path"]))
        existing = operations.pop(key, None)
        if existing is None:
            raise ValueError(f"correction targets {key}, which is not in the golden contract")
        # The route is still served, so it stays in the contract and its existence
        # is still asserted; only the documented body is dropped, because the fork
        # no longer returns it.
        operations[key] = Operation(
            method=key[0], path=key[1], required_params=existing.required_params
        )

    for entry in corrections.get("response_field_types", []):
        key = (entry["method"].upper(), normalise_path(entry["path"]))
        existing = operations.get(key)
        if existing is None:
            raise ValueError(f"correction targets {key}, which is not in the golden contract")
        field_name = entry["field"]
        if field_name not in existing.response_fields:
            raise ValueError(
                f"type correction for {key} names {field_name!r}, which the golden "
                f"contract does not declare — the entry is stale"
            )
        corrected_type = frozenset(entry["type"])
        types = dict(existing.field_types)
        if types.get(field_name) == corrected_type:
            raise ValueError(
                f"type correction for {key}.{field_name} changes nothing; upstream's "
                "annotation now agrees with the served type, so delete the entry"
            )
        types[field_name] = corrected_type
        operations[key] = Operation(
            method=existing.method,
            path=existing.path,
            response_fields=existing.response_fields,
            required_params=existing.required_params,
            field_types=frozenset(types.items()),
        )

    for entry in corrections.get("add", []):
        key = (entry["method"].upper(), normalise_path(entry["path"]))
        if key in operations:
            raise ValueError(f"correction adds {key}, which the golden contract already has")
        # Undocumented upstream, so there is nothing to diff the body against.
        # Registering the operation still lets us assert the route exists.
        operations[key] = Operation(method=key[0], path=key[1])

    for entry in corrections.get("response_fields", []):
        key = (entry["method"].upper(), normalise_path(entry["path"]))
        existing = operations.get(key)
        if existing is None:
            raise ValueError(f"correction targets {key}, which is not in the golden contract")
        corrected = frozenset(entry["fields"])
        if corrected == existing.response_fields:
            raise ValueError(
                f"correction for {key} changes nothing; upstream's annotation now agrees "
                "with the served body, so delete the entry"
            )
        operations[key] = Operation(
            method=existing.method,
            path=existing.path,
            response_fields=corrected,
            required_params=existing.required_params,
        )

    return operations


def load_phase1_whitelist() -> list[OperationKey]:
    data = yaml.safe_load(WHITELIST_PATH.read_text())
    return [
        (entry["method"].upper(), normalise_path(entry["path"])) for entry in data["operations"]
    ]


def load_aliases() -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = (yaml.safe_load(ALIASES_PATH.read_text()) or {}).get(
        "aliases", []
    )
    return aliases


def golden_operations() -> dict[OperationKey, Operation]:
    """Alias kept for readability at call sites."""
    return load_golden()
