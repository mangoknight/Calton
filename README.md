# Calton

A task management API server — a [FastAPI](https://fastapi.tiangolo.com/) + SQLAlchemy
rewrite of the Vikunja Go backend, with a React frontend (`web-react/`).

## Architecture

- **Backend**: `server/` — FastAPI + SQLAlchemy + Alembic migrations, running under `uv`
- **Frontend**: `web-react/` — React + Vite + TypeScript
- **Python**: 3.12, managed by [uv](https://docs.astral.sh/uv/)

## Quick start

### Backend

```bash
cd server
uv sync
uv run uvicorn calton.main:create_app --factory --reload --port 3456
```

### Frontend

```bash
cd web-react
npm install
npm run dev
```

### Docker

```bash
docker build -f server/Dockerfile -t calton .
docker run -p 3456:3456 -v calton-data:/data calton
```

## Configuration

Configuration is via environment variables (prefix `CALTON_`) or a `config.yml` file.

| Variable | Default | Description |
|---|---|---|
| `CALTON_DATABASE_PATH` | `vikunja.db` | SQLite database path |
| `CALTON_FILES_BASEPATH` | `./files` | File storage directory |
| `CALTON_SERVICE_SECRET` | required | JWT signing secret (min 24 chars) |
| `CALTON_SERVICE_TESTINGTOKEN` | unset | Testing endpoint token |

## Development

```bash
cd server
uv run pytest -q                    # run tests
uv run mypy --strict src tests      # type check
uv run ruff check src tests         # lint
uv run ruff format src tests        # format
```

## License

AGPLv3
