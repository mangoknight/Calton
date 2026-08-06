# The golden contract

`calton-v1-swagger.json` is a frozen copy of `pkg/swagger/swagger.json` taken at
commit `627aa46` — the upstream file has sha256 `feb3f68b…` and is 425 628 bytes
(Swagger 2.0, basePath `/api/v1`, 126 paths / 169 operations / 98 definitions).

**One field is edited.** Go's swag leaves `info.version` null, which forces
`openapi-typescript` into patch mode on the frontend. It is set here to
`v1-frozen-feb3f68b`, naming the sha256 prefix of the upstream file it was taken
from — so the provenance is still checkable, and re-freezing must update it.
`test_the_golden_file_matches_upstream_except_for_the_version` enforces that this
is the *only* difference.

It is frozen rather than read from `pkg/` so that a change to the Go side shows up
as a deliberate contract update in review, not as a silently moving target. The
frontend generates its TypeScript types from this same file, so re-freezing it is
a cross-team event.

## The golden contract is not the last word — routes.go is

The swagger is generated from `@Router` annotations in the Go sources, and three
of those annotations disagree with the routes actually registered in
`pkg/routes/routes.go`. Where they conflict, **the registered route wins**: that
is what clients actually talk to. The divergences are listed in
`swagger-corrections.yaml`, each with the evidence for the call, and are applied
to the golden contract before any diffing happens.

Two of the three are annotations that were never written, so the operation is
missing from the swagger entirely. The third is a wrong verb, which matters more
than it looks: it would have had us implement label updates as `PUT /labels/{id}`
when the server actually serves `POST /labels/{label}`. A wrong verb in v1 shows
up as a 404, not as an error message.

## Files

| File | What it is |
|---|---|
| `calton-v1-swagger.json` | Frozen upstream contract. Do not hand-edit. |
| `swagger-corrections.yaml` | Where the swagger disagrees with routes.go, and why. |
| `phase1-endpoints.yaml` | The 68 operations Phase 1 promises. Keyed by method+path. |
| `aliases.yaml` | Paths Calton serves that upstream does not, for client compatibility. |

## Why the whitelist is keyed on method + path

The design called for an `operationId` whitelist, but only 1 of the 169
operations in the swagger has an `operationId`. Method plus path is the only key
that actually exists for all of them.
