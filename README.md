# Dropbox MVP

[![Lint](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/lint.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/lint.yml)
[![CI](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/ci.yml)
[![Functional](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/functional.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/functional.yml)

Backend MVP implementing Dropbox's core file-sync primitives: block-level deduplication with content-addressed storage, two-phase upload, revision-based conflict detection, soft-delete with reference counting, and file sharing.

## Quickstart

```bash
git clone https://github.com/iliazlobin/sd-dropbox-backend-mvp.git
cd sd-dropbox-backend-mvp

# Start the stack (requires Docker)
docker compose up -d --build --wait
docker compose run --rm -T app alembic upgrade head

# Verify
curl http://localhost:8010/healthz
# → {"status":"ok"}
```

## API Reference

All endpoints accept/return JSON. Mock auth via `X-User-Id` header on sharing endpoints.

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| `GET` | `/healthz` | Liveness probe | 200 |
| `POST` | `/files/commit` | Upload/update file blocklist; returns `need_blocks` for two-phase commit | 201 |
| `GET` | `/files/{file_id}` | Get file metadata + ordered blocklist | 200 |
| `GET` | `/files/{file_id}/metadata` | Get file metadata only (block count, size, revision, deleted flag) | 200 |
| `GET` | `/files/list?namespace_id={ns}` | List non-deleted files in a namespace | 200 |
| `POST` | `/files/delete` | Soft-delete a file; decrements block reference counts | 200 |
| `POST` | `/blocks/put` | Upload a 4 MB block (base64); idempotent by SHA-256 hash | 201 |
| `GET` | `/blocks/{block_hash}` | Download a block by SHA-256 hash | 200 |
| `POST` | `/sharing/add` | Share a file with another user (reader access) | 201 |
| `GET` | `/sharing/list?user_id={uid}` | List files shared with a user | 200 |

**Error codes:** `404` (not found), `409` (conflict — stale revision, self-share, duplicate share), `422` (validation — missing fields, hash mismatch, invalid base64), `401` (missing `X-User-Id` on sharing endpoints), `403` (shared reader attempting delete).

### Key flows

**Two-phase upload:** `POST /files/commit` with a blocklist → server returns `need_blocks` (hashes not yet stored). Upload missing blocks via `POST /blocks/put`. Re-commit the same blocklist → `need_blocks` is empty, file row written atomically.

**Download:** `GET /files/{file_id}` returns the ordered blocklist. Fetch each block via `GET /blocks/{block_hash}`, concatenate in order.

**Conflict detection:** Every commit/delete includes a `parent_revision`. If it doesn't match the current revision, the server returns `409 {error: "Conflict", current_revision: N}`.

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable | Default | Description |
|---|---|---|
| `DROPBOX_DATABASE_URL` | `postgresql+asyncpg://dropbox:dropbox@localhost:5432/dropbox` | PostgreSQL async connection string |
| `DROPBOX_BLOCK_STORAGE_DIR` | `data/blocks` | Content-addressed block storage path |
| `APP_PORT` | `8010` | Host port for `docker compose` (compose var) |

## Testing

```bash
# Unit tests (no DB needed)
pytest tests/unit/ -v

# Functional tests (requires PostgreSQL + migrations)
pytest tests/functional/ -v

# Acceptance tests (black-box, against running system)
docker compose up -d --build --wait
docker compose run --rm -T app alembic upgrade head
API_BASE_URL=http://localhost:8000 pytest verify/acceptance/ -v
```

Test structure:
- `tests/unit/` — isolated service-layer tests (mocked DB)
- `tests/functional/` — in-process endpoint scenarios via `httpx.ASGITransport` with real PostgreSQL
- `verify/acceptance/` — black-box HTTP contract tests (one per functional requirement)

## Project Layout

```
src/dropbox/
├── main.py              # FastAPI app factory + lifespan + /healthz
├── config.py             # pydantic-settings (env-prefixed)
├── database.py           # SQLAlchemy async engine + session dependency
├── routers/
│   ├── files.py          # /files/* endpoints
│   ├── blocks.py         # /blocks/* endpoints
│   └── sharing.py        # /sharing/* endpoints
├── services/
│   ├── file_service.py   # Commit, list, delete, metadata logic
│   ├── block_service.py  # Store, retrieve, verify, dedup logic
│   └── sharing_service.py # Share, list-shares, access-check logic
├── models/
│   ├── file.py           # File ORM (file_id, namespace_id, path, blocklist, revision, is_deleted, size)
│   ├── block.py          # Block ORM (block_hash PK, size, ref_count)
│   └── share.py          # Share ORM (share_id, file_id FK, owner_id, shared_with, access_type)
└── schemas/
    ├── file.py           # CommitRequest, FileResponse, FileMetadataResponse, etc.
    ├── block.py          # BlockPutRequest, BlockResponse
    └── sharing.py        # AddShareRequest, ShareResponse, ShareListItem

alembic/                  # Alembic migrations (async)
tests/                    # White-box tests (unit + functional)
verify/acceptance/        # Black-box acceptance tests (one per FR)
data/blocks/              # Local filesystem block storage (SHA-256 content-addressed)
```

**Architecture:** Router → Service → Model. Routers parse/validate HTTP and serialize responses; services contain all business logic; models are SQLAlchemy 2.0 ORM classes backed by PostgreSQL. Block storage uses the local filesystem at `data/blocks/<sha256-hex>` (content-addressed by the first 4 hex chars as directory shards).

## Limitations

- **Auth is mock:** `X-User-Id` header only. No OAuth, JWT, or session management.
- **Polling, not push:** No WebSocket notifications. Clients must poll `GET /files/list` for changes.
- **Fixed block size:** All blocks are 4 MB. No variable-size or content-defined chunking.
- **No garbage collection:** Blocks with `ref_count = 0` remain on disk indefinitely.
- **No delta sync:** Entire blocklist is sent on every commit; no content-hash comparison.
- **Local storage only:** Blocks stored on local filesystem, not Magic Pocket / S3.
- **No thumbnail/preview generation.**
- **No full-text search.**
