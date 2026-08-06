# Calton server

A Python/FastAPI reimplementation of the Calton v1 API. The Go code in the repository
root stays untouched — it is the reference implementation the parity suite compares
against.

## Development

```sh
cd server
uv sync --all-groups
uv run uvicorn calton.main:create_app --factory --reload --port 3456
```

Configuration uses upstream key names, so `CALTON_SERVICE_SECRET`, `CALTON_DATABASE_PATH`
and friends work unchanged. See `src/calton/config.py`.

## Checks

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest
```

CI (`.github/workflows/calton.yml`) runs four jobs: `lint`, `unit`, `contract` and
`parity`. The last two are wired up but stay empty until T06 and T10 land.

## Layout

| Path | Contents |
| --- | --- |
| `src/calton/` | Application code |
| `tests/unit/` | Unit tests |
| `tests/contract/` | OpenAPI diff against the frozen v1 swagger (T06) |
| `tests/parity/` | Differential tests against the Go server (T10) |
| `contract/` | Frozen swagger, Phase 1 endpoint allowlist, alias registry (T06) |
| `scripts/` | Developer and CI helper scripts |
