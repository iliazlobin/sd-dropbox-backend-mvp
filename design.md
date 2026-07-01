# Dropbox MVP — Design & Specification

Backend implementation of Dropbox's core file-sync primitives: block-level deduplication, content-addressed storage, two-phase upload, revision-based conflict detection, soft-delete with reference counting, and file sharing. The full target design is the System Design: Dropbox; this MVP implements the subset of primitives needed to exercise the upload/download/sync loop end-to-end.

## 1. Architecture

```mermaid
graph TB
    subgraph api[FastAPI App — port 8000]
        R_FILES[Files Router]
        R_BLOCKS[Blocks Router]
        R_SHARE[Sharing Router]
        HZ[/healthz]
    end

    subgraph services[Service Layer]
        FS[FileService]
        BS[BlockService]
        SS[SharingService]
    end

    subgraph data[Data Layer]
        PG[(PostgreSQL<br/>SQLAlchemy 2.0)]
        FSYS["data/blocks/<br/>Local FS"]
    end

    R_FILES --> FS
    R_BLOCKS --> BS
    R_SHARE --> SS
    FS --> PG
    BS --> PG
    BS --> FSYS
    SS --> PG

    classDef svc   fill:#d0ebff,stroke:#1c7ed6,color:#1a1a1a;
    classDef store fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a;

    class R_FILES,R_BLOCKS,R_SHARE,HZ svc
    class FS,BS,SS svc
    class PG,FSYS store
```

**Layers:** Router (HTTP parse/validate/serialize, no business logic) -> Service (business logic + data access) -> Model (SQLAlchemy 2.0 ORM, PostgreSQL). This is the standard FastAPI three-layer split.

**Block storage** uses the local filesystem at `data/blocks/<sha256-hex>`, simulating Magic Pocket. Blocks are content-addressed by SHA-256 hex digest with 2-level directory sharding (`data/blocks/ab/cd/<full-hash>`).

**No WebSockets, no notification pods.** File change detection uses polled `GET /files/list?namespace_id=`.

## 2. Key Design Decisions

### D1: Two-phase commit with retry

**Decision:** Two-phase upload — client sends blocklist, server reports `need_blocks`, client uploads missing blocks, client re-commits.

**Rationale:** Matches Dropbox's production upload flow. Deduplication is server-side: the client never uploads a block already stored. When updating a file that is 90% identical to a prior revision, only new blocks traverse the network. Single atomic upload (all blocks inline) would re-upload every block on every revision.

**Trade-off:** Two HTTP round-trips vs. one. The bandwidth savings from deduplication justify the extra latency — without it, every file revision re-uploads all blocks.

### D2: PostgreSQL ARRAY for blocklist

**Decision:** `blocklist TEXT[]` column on `files`, not a join table.

**Rationale:** The blocklist is always read and written as an ordered whole — "give me all blocks for this file." An ARRAY avoids a JOIN on every file read. The full Dropbox design stores blocklists as blobs in the Server File Journal (SFJ); the ARRAY is the closest relational analog. If block-level queries ("which files contain hash X?") are needed later, a migration to a join table is straightforward.

### D3: Soft-delete with ref_count decrement

**Decision:** Set `is_deleted = true`, decrement block `ref_count`. No hard-delete.

**Rationale:** Enables undo/restore later. The `ref_count` mechanism is the same one Magic Pocket uses for garbage collection. Decrementing on delete keeps block storage accounting accurate even without a GC sweep. Blocks with `ref_count = 0` accumulate until a GC job runs — negligible for MVP scale.

### D4: Revision-based optimistic concurrency

**Decision:** `parent_revision` integer on every commit/delete. Stale returns `409 Conflict {current_revision: N}`.

**Rationale:** Prevents silent data loss. This is Dropbox's production model — clients that see a 409 rename the local file as "filename (conflicted copy).ext" and upload as a new entry. Last-write-wins would be simpler but drops data.

### D5: Mock auth via X-User-Id header

**Decision:** `X-User-Id` header for caller identity on sharing endpoints; `namespace_id` explicit in file operation bodies.

**Rationale:** Sharing needs caller identity separate from operation scope. Production OAuth/JWT is out of MVP scope. The header establishes caller identity without coupling it to the request body.

### D6: Separate block endpoints

**Decision:** `POST /blocks/put` and `GET /blocks/{hash}` independent of `POST /files/commit`.

**Rationale:** Blocks are the unit of deduplication and transfer. Separating them lets clients download individual blocks for file reconstruction, upload blocks independently of commits, and enables future block-level operations (LAN Sync, block caching).

### D7: Share ownership on the Share row

**Decision:** `owner_id` stored on the Share row, derived from `X-User-Id` at share creation time. No `owner_id` on File.

**Rationale:** A file can have multiple owners in the future (shared folders). Ownership is a relationship, not a property of the file. For MVP, ownership is implicit: the user who first uploads a file is the de facto owner (they hold the `parent_revision`).

## 3. Data Model

```sql
File {
  file_id:      uuid PK
  namespace_id: bigint    -- user/team scope
  path:         text
  blocklist:    text[]    -- ordered SHA-256 hex hashes
  revision:     integer   -- monotonic per file, used for conflict detection
  is_deleted:   boolean
  size:         bigint    -- len(blocklist) x 4 MB
  modified_at:  timestamp
}

-- Partial unique index: one path per namespace for non-deleted files
CREATE UNIQUE INDEX ix_files_ns_path ON files (namespace_id, path)
  WHERE NOT is_deleted;

Block {
  block_hash:  text PK    -- SHA-256 hex (64 chars)
  size:        bigint     -- fixed 4 MB for MVP
  ref_count:   integer    -- active file revisions referencing this block
  stored_at:   timestamp
}

Share {
  share_id:    uuid PK
  file_id:     uuid FK -> File
  owner_id:    bigint
  shared_with: bigint
  access_type: varchar(20) -- reader | editor | viewer (MVP exercises reader only)
  created_at:  timestamp
  UNIQUE(file_id, shared_with)
}
```

**Design notes:**
- `blocklist` as `ARRAY(Text)` avoids a join table for the common read path (fetch all blocks for a file).
- `ref_count` on Block mirrors Magic Pocket's garbage-collection pattern — count-based, not join-counted.
- Partial unique index on `(namespace_id, path) WHERE NOT is_deleted` allows soft-deleted files to leave their path slot occupied.
- `namespace_id` uses `bigint` (matching the full design's namespace model); MVP uses arbitrary mock IDs.
- No `content_hash` — delta sync is out of MVP scope.

## 4. API Reference

All endpoints return JSON. Mock auth via `X-User-Id` header on sharing endpoints.

### Health

`GET /healthz` — Liveness probe. Returns `200 {"status":"ok"}`.

### Files

`POST /files/commit` — Create or update a file via two-phase blocklist commit.

```
Request:  {namespace_id: int, path: str, blocklist: [str], parent_revision: int|null}
201:      {file_id: uuid, revision: int, need_blocks: [str]}
409:      {error: "Conflict", current_revision: N}
422:      Missing required fields
```

First call returns `need_blocks` for hashes not yet stored. After uploading missing blocks via `POST /blocks/put`, re-commit with the same payload — all blocks exist, so the file row is created atomically, revision bumped, block ref_counts incremented.

`GET /files/{file_id}` — Get file metadata + ordered blocklist.

```
200:  {file_id: uuid, path: str, blocklist: [str], revision: int, size: int, modified_at: ISO8601}
404:  File not found or is_deleted
```

`GET /files/{file_id}/metadata` — Get file metadata only (no blocks).

```
200:  {file_id: uuid, path: str, block_count: int, size: int, revision: int, modified_at: ISO8601, is_deleted: bool}
404:  File not found
```

`GET /files/list?namespace_id={ns}` — List non-deleted files in a namespace, ordered by path.

```
200:  [{file_id: uuid, path: str, revision: int, size: int, modified_at: ISO8601}, ...]
     Empty namespace returns []
```

`POST /files/delete` — Soft-delete a file.

```
Request:  {namespace_id: int, file_id: uuid, parent_revision: int}
200:      {file_id: uuid, deleted: true}
404:      File not found or already deleted
409:      Conflict — parent_revision mismatch
403:      Caller is a shared reader (not owner)
```

Sets `is_deleted = true`, decrements `ref_count` for each block in the file's blocklist.

### Blocks

`POST /blocks/put` — Upload a 4 MB block (base64-encoded).

```
Request:  {block_hash: str, data: str}   // data: base64-encoded raw bytes
201:      {block_hash: str, status: "stored"|"already_exists"}
422:      Invalid base64 or SHA-256(data) != block_hash
```

Idempotent: uploading the same hash twice returns `"already_exists"`; no duplicate storage or DB row.

`GET /blocks/{block_hash}` — Download a block by hash.

```
200:  {block_hash: str, data: str}   // base64-encoded raw bytes
404:  Block not found in storage or DB
```

### Sharing

`POST /sharing/add` — Share a file with another user.

```
Request:  {file_id: uuid, user_id: int, access_type: "reader"}
Headers:  X-User-Id: <owner_id>
201:      {share_id: uuid, file_id: uuid, owner_id: int, shared_with: int, access_type: str}
404:      File not found
409:      Self-share (owner_id == user_id) or already shared
401:      Missing X-User-Id header
```

`GET /sharing/list?user_id={uid}` — List files shared with a user.

```
200:  [{file_id: uuid, path: str, owner_id: int, access_type: str}, ...]
     No shares returns []
```

## 5. Functional Requirements -> Acceptance Tests

Each FR maps to one black-box acceptance test in `verify/acceptance/`. All eight must pass against the running system.

| FR | Requirement | Test file | What it proves |
|----|------------|-----------|----------------|
| FR-1 | Upload with deduplication | `test_fr1_upload_dedup.py` | Two-phase commit flow; second commit of same blocklist returns empty `need_blocks`; block count = 3 |
| FR-2 | Download / reconstruct | `test_fr2_download_reconstruct.py` | GET file returns blocklist; GET each block; reconstructed content matches original |
| FR-3 | List files in namespace | `test_fr3_list_files.py` | 3 files created -> 3 listed; 1 deleted -> 2 listed; empty namespace -> [] |
| FR-4 | Soft-delete | `test_fr4_soft_delete.py` | Delete -> file excluded from list; GET /files/{id} -> 404; block ref_counts decremented |
| FR-5 | Conflict detection | `test_fr5_conflict_detection.py` | Commit with stale parent_revision -> 409 with current_revision |
| FR-6 | Share file | `test_fr6_share_file.py` | Share with reader -> 201; self-share -> 409; reader can GET metadata; reader cannot delete |
| FR-7 | List shared files | `test_fr7_list_shares.py` | Share 2 files -> list returns 2; no shares -> [] |
| FR-8 | File metadata | `test_fr8_file_metadata.py` | GET metadata -> block_count, size, revision, is_deleted fields correct |

## 6. Test Results

Continuous integration runs three workflows on every push and daily on schedule:

| Workflow | Badge |
|----------|-------|
| Lint (ruff v0.8.0) | [![Lint](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/lint.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/lint.yml) |
| CI (unit + e2e acceptance) | [![CI](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/ci.yml) |
| Functional | [![Functional](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/functional.yml/badge.svg)](https://github.com/iliazlobin/sd-dropbox-backend-mvp/actions/workflows/functional.yml) |

**Test taxonomy:**

- **Unit tests** (`tests/unit/`) — Isolated service-layer tests. File service (commit, conflict, delete, list), block service (store, hash validation, idempotent upload), sharing service (add, list, self-share rejection).
- **Functional tests** (`tests/functional/`) — In-process endpoint scenarios using `httpx.ASGITransport` with real PostgreSQL. Covers all 8 FRs as a single comprehensive test file exercising the full upload->download->delete->share loop.
- **Acceptance tests** (`verify/acceptance/`) — Black-box HTTP contract tests. One file per FR, talking to the running system at `API_BASE_URL`. Uses unique namespace/user IDs per test for isolation.

## 7. Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.12, FastAPI, uvicorn |
| Datastore | PostgreSQL 16 via SQLAlchemy 2.0 (async) + Alembic |
| Block storage | Local filesystem (`data/blocks/`, content-addressed by SHA-256) |
| Tests | pytest + httpx (ASGITransport for functional, HTTP for acceptance) |
| Linting | ruff 0.8.0 |
| Container | Docker Compose (app + postgres), multi-stage Dockerfile on `python:3.12-slim` |
| CI | GitHub Actions (lint, ci, functional — on push + daily schedule) |
