# Dropbox MVP — Deploy Guide

## Prerequisites

- Docker Engine 24+ with Compose v2
- Python 3.12 (for local development)
- Git

## Quick Start

```bash
# Clone
git clone https://github.com/iliazlobin/sd-dropbox-backend-mvp.git
cd sd-dropbox-backend-mvp

# Start the stack
docker compose up -d --build --wait

# Run migrations
docker compose run --rm -T app alembic upgrade head

# Verify
curl http://localhost:8010/healthz
# → {"status":"ok"}
```

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable | Default | Description |
|---|---|---|
| `DROPBOX_DATABASE_URL` | `postgresql+asyncpg://dropbox:dropbox@localhost:5432/dropbox` | PostgreSQL async connection string |
| `DROPBOX_APP_PORT` | `8000` | In-container listen port |
| `DROPBOX_BLOCK_STORAGE_DIR` | `data/blocks` | Content-addressed block storage path |

Override the host port via `APP_PORT`:

```bash
APP_PORT=8020 docker compose up -d
```

## Stack

```
app (FastAPI + uvicorn) ── port 8000:${APP_PORT:-8010}
  │
  └── db (PostgreSQL 16)
```

Only `app` publishes a host port. `db` communicates over the compose network.

## Healthcheck

```bash
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/healthz')"
```

The slim runtime image has no `curl` — the healthcheck uses Python's stdlib.

## Run Tests

```bash
# Unit tests (no DB needed)
docker compose run --rm -T app pytest tests/unit/ -v

# Functional tests (needs DB + migrations)
docker compose up -d --wait
docker compose run --rm -T app alembic upgrade head
docker compose run --rm -T app pytest tests/functional/ -v

# Acceptance tests (black-box, against live system)
docker compose up -d --build --wait
docker compose run --rm -T app alembic upgrade head
API_BASE_URL=http://localhost:8000 pytest verify/acceptance/ -v
```

## Teardown

```bash
docker compose down --volumes --remove-orphans
```

## CI/CD

GitHub Actions runs three workflows on every push and daily on schedule:

| Workflow | What | Gate |
|---|---|---|
| `lint.yml` | ruff check + format (v0.8.0) | hard |
| `ci.yml` | unit tests + e2e acceptance | hard |
| `functional.yml` | functional tests (own Postgres) | hard |
| Copilot Code Review | automated PR review | advisory |

Copilot Code Review is an advisory check — it comments on PRs but does not block merges.

## Local Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn dropbox.main:app --host 0.0.0.0 --port 8000
```
