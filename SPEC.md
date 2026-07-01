# Dropbox MVP — Build Spec

## 1. Goal & scope

Build a backend MVP implementing Dropbox's core file-sync primitives: upload with block-level deduplication,
download/reconstruction, namespace-scoped file listing, soft-delete, revision-based conflict detection, and
file sharing. The MVP uses local filesystem block storage in place of Magic Pocket, and polling in place of
real-time WebSocket notifications.

**In scope**
- Fixed 4 MB block chunking with SHA-256 content-addressed deduplication
- Two-phase upload (commit blocklist → server reports missing blocks → upload blocks → recommit)
- File download via ordered block reconstruction
- Namespace-scoped file listing (non-deleted files only)
- Soft-delete with block reference counting
- Revision-based optimistic concurrency (409 Conflict on stale parent_revision)
- File sharing with reader access + shared-files listing
- File metadata endpoint

**Out of scope**
- Real-time WebSocket notifications (polling model)
- Full-text search (Nautilus)
- Thumbnail / preview generation
- LAN Sync peer discovery
- Erasure coding / cold storage tiering
- Cross-zone replication
- OAuth / production auth (mock user header)
- Client-side delta sync (server-side only)

## 2. Functional requirements

- **FR-1 — Upload with deduplication.** Client POSTs ordered blocklist → server returns `need_blocks` (hashes not yet stored). Client uploads missing blocks via `/blocks/put` (idempotent — SHA-256 collision is a no-op). Client recommits → server atomically writes file row + bumps revision.
  `POST /files/commit {namespace_id, path, blocklist, parent_revision}` → `201 {file_id, revision, need_blocks}`; missing fields → `422`.
- **FR-2 — Download file.** Retrieve file metadata + blocklist, then fetch each block in order to reconstruct.
  `GET /files/{file_id}` → `200 {file_id, path, blocklist, revision, size, modified_at}`; not found → `404`.
  `GET /blocks/{block_hash}` → `200 {block_hash, data: base64}`.
- **FR-3 — List files in namespace.** Return all non-deleted files for a namespace.
  `GET /files/list?namespace_id={ns}` → `200 [{file_id, path, revision, size, modified_at}, ...]`.
- **FR-4 — Soft-delete file.** Mark file deleted; decrement block reference counts.
  `POST /files/delete {namespace_id, file_id, parent_revision}` → `200`; conflict → `409`; already deleted → `404`.
- **FR-5 — Conflict detection.** Reject commits with stale revision.
  `POST /files/commit` with outdated `parent_revision` → `409 {error: "Conflict", current_revision: N}`.
- **FR-6 — Share file.** Grant read access to another user.
  `POST /sharing/add {file_id, user_id, access_type: "reader"}` → `201`; self-share → `409`; not found → `404`.
- **FR-7 — List shared files.** Files shared with the calling user.
  `GET /sharing/list?user_id={uid}` → `200 [{file_id, path, owner_id, access_type}, ...]`.
- **FR-8 — File metadata.** Lightweight endpoint returning file details without blocks.
  `GET /files/{file_id}/metadata` → `200 {file_id, path, block_count, size, revision, modified_at, is_deleted}`.

## 3. Stack & deployment

- **Runtime:** Python 3.12, FastAPI, uvicorn
- **Datastore:** PostgreSQL (file metadata, block index, sharing ACLs) via SQLAlchemy 2.0 + Alembic
- **Block storage:** Local filesystem under `data/blocks/` (content-addressed by SHA-256 hex)
- **Tests:** pytest + httpx.ASGITransport (functional) + HTTP (black-box acceptance)
- **Container:** Docker Compose (app + postgres)

Design → [System Design: Dropbox](https://app.notion.com/p/System-Design-Dropbox-v2026-06-28-1-38cd865005a88177a4e1ce28111dcdec)

## 4. Data model

```sql
File {
  file_id:      uuid PK
  namespace_id: bigint    ← user/team scope
  path:         text
  blocklist:    text[]    ← ordered SHA-256 hex hashes
  revision:     integer   ← monotonic per file
  is_deleted:   boolean
  size:         bigint    ← total bytes (block_count × 4 MB)
  modified_at:  timestamp
}

Block {
  block_hash:  text PK   ← SHA-256 hex
  size:        bigint    ← always 4194304 (4 MB) for MVP
  ref_count:   integer   ← active file revisions referencing this block
  stored_at:   timestamp
}

Share {
  share_id:    uuid PK
  file_id:     uuid FK → File
  owner_id:    bigint
  shared_with: bigint
  access_type: enum     ← reader | editor | viewer
  created_at:  timestamp
  UNIQUE(file_id, shared_with)  ← one share per user per file
}
```

## 5. API

- `POST /files/commit` — upload blocklist, create or update file revision; returns `need_blocks`
- `POST /blocks/put` — upload a raw 4 MB block (base64); idempotent by SHA-256
- `GET /blocks/{block_hash}` — download a block by hash
- `GET /files/{file_id}` — get file metadata + blocklist
- `GET /files/{file_id}/metadata` — get file metadata only
- `GET /files/list?namespace_id={ns}` — list non-deleted files in namespace
- `POST /files/delete` — soft-delete a file
- `POST /sharing/add` — share a file with another user
- `GET /sharing/list?user_id={uid}` — list files shared with user

## 6. Test scenarios

- Deduplication: same blocklist committed twice → second commit returns empty `need_blocks`, block count unchanged
- Idempotent block upload: PUT same block twice → both return 201, one row in blocks table
- Conflict: commit with stale parent_revision → 409, current revision returned
- Soft-delete: delete file → list excludes it, block ref_counts decrement
- Share access control: shared reader can GET metadata, cannot delete
- Concurrent commits: two uploads with correct revision → both succeed serially
- Validation: missing path → 422, invalid block_hash format → 422
- Cross-namespace isolation: namespace A files not visible in namespace B

## 7. Module layout

```
src/
  main.py              # FastAPI app, lifespan
  config.py             # pydantic-settings
  database.py           # SQLAlchemy engine + session
  routers/
    files.py            # /files/* endpoints
    blocks.py           # /blocks/* endpoints
    sharing.py          # /sharing/* endpoints
  services/
    file_service.py     # commit, list, delete, metadata logic
    block_service.py    # store, retrieve, dedup logic
    sharing_service.py  # share, list-shares, access-check logic
  models/
    file.py             # File ORM model
    block.py            # Block ORM model
    share.py            # Share ORM model
  schemas/
    file.py             # Pydantic request/response schemas
    block.py            # Block schemas
    sharing.py          # Sharing schemas
alembic/
  versions/             # migrations
tests/
  unit/                 # isolated unit tests
  functional/           # in-process endpoint scenarios (httpx.ASGITransport)
verify/
  acceptance/           # black-box HTTP acceptance tests (one per FR)
data/
  blocks/               # local filesystem block storage
```

## 8. Run

```bash
docker compose up -d
curl http://localhost:8000/healthz
pytest tests/unit/ tests/functional/ -v
pytest verify/acceptance/ -v
```
