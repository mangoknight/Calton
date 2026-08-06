"""Diff Calton's generated OpenAPI against the golden contract.

The rule is superset, not equality: Calton may return fields upstream does not
(the `right`/`max_right` double-write in core/compat.py exists precisely to do
that), but it may never *drop* a field a client expects, and it may not require a
parameter upstream leaves optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from calton.contract.golden import (
    Operation,
    OperationKey,
    _operations_of,
    normalise_path,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

API_PREFIX = "/api/v1"


@dataclass(frozen=True)
class OperationDiff:
    key: OperationKey
    missing_operation: bool = False
    missing_response_fields: frozenset[str] = frozenset()
    extra_required_params: frozenset[str] = frozenset()
    widened_field_types: frozenset[tuple[str, str, str]] = frozenset()
    """``(field, upstream types, our types)`` where we declare a type upstream does not.

    Same superset philosophy as the rest of this file, one level down: extra
    *fields* are allowed, a wider *type* is not. Declaring ``anyOf: [integer,
    number]`` where upstream says ``number`` gives a generated client a type that
    admits values the real API never sends — and unlike a wrong value, which makes
    a client throw, a wrong type makes it quietly accept the wrong thing.

    Narrowing is not reported. A response that only ever produces ``string`` where
    upstream declares ``string|null`` is still readable by every client written
    against upstream, so it is not an incompatibility.
    """

    @property
    def ok(self) -> bool:
        return not (
            self.missing_operation
            or self.missing_response_fields
            or self.extra_required_params
            or self.widened_field_types
        )

    def describe_if_broken(self) -> str:
        """``describe()`` when something is wrong, and empty when nothing is.

        Lets a caller drop a category (a registered exemption) and reuse the same
        message, instead of reimplementing ``ok`` and the formatting side by side —
        which is how the two drift and a check starts passing for the wrong reason.
        """
        return "" if self.ok else self.describe()

    def describe(self) -> str:
        method, path = self.key
        if self.missing_operation:
            return f"{method} {path}: not implemented"

        problems = []
        if self.missing_response_fields:
            problems.append(f"response is missing {sorted(self.missing_response_fields)}")
        if self.extra_required_params:
            problems.append(
                f"requires {sorted(self.extra_required_params)}, which upstream leaves optional"
            )
        if self.widened_field_types:
            widened = "; ".join(
                f"{name}: upstream {theirs}, ours {ours}"
                for name, theirs, ours in sorted(self.widened_field_types)
            )
            problems.append(f"widens {widened}")
        return f"{method} {path}: " + "; ".join(problems) if problems else f"{method} {path}: ok"


def generated_operations(app: FastAPI) -> dict[OperationKey, Operation]:
    """Reduce the app's own OpenAPI to the same shape as the golden contract.

    Paths are stripped of the /api/v1 prefix so both sides use upstream's
    basePath-relative spelling.
    """
    spec: dict[str, Any] = app.openapi()
    operations = _operations_of(spec)

    stripped: dict[OperationKey, Operation] = {}
    for (method, path), operation in operations.items():
        relative = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
        relative = normalise_path(relative)
        stripped[(method, relative)] = Operation(
            method=method,
            path=relative,
            response_fields=operation.response_fields,
            required_params=operation.required_params,
            field_types=operation.field_types,
        )
    return stripped


def diff_operation(
    key: OperationKey,
    golden: dict[OperationKey, Operation],
    generated: dict[OperationKey, Operation],
) -> OperationDiff:
    ours = generated.get(key)
    if ours is None:
        return OperationDiff(key=key, missing_operation=True)

    theirs = golden.get(key)
    if theirs is None:
        # Calton-only, e.g. the /tasks/all alias. Nothing upstream to diff against.
        return OperationDiff(key=key)

    return OperationDiff(
        key=key,
        missing_response_fields=theirs.response_fields - ours.response_fields,
        extra_required_params=ours.required_params - theirs.required_params,
        widened_field_types=_widened_types(theirs, ours),
    )


def _widened_types(theirs: Operation, ours: Operation) -> frozenset[tuple[str, str, str]]:
    """Fields where we admit a type upstream does not declare.

    Only fields present on **both** sides are compared: a field we add on purpose
    has no upstream type to widen, and an upstream field we do not implement is
    already reported as `missing_response_fields`.

    A side declaring *no* type is skipped rather than treated as a mismatch. Both
    generators emit bare `{}` for an untyped body, and upstream's swaggo output
    leaves several fields untyped; reading that as "upstream declares nothing, so
    everything we declare is a widening" would report the whole response body.
    """
    theirs_types = theirs.types_by_field
    ours_types = ours.types_by_field

    widened = set()
    for name, upstream in theirs_types.items():
        mine = ours_types.get(name, frozenset())
        if not upstream or not mine:
            continue
        extra = mine - upstream
        if extra:
            widened.add((name, "|".join(sorted(upstream)), "|".join(sorted(mine))))
    return frozenset(widened)
