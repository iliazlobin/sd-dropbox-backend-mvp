# Dropbox MVP — Architecture Design

> Adapts the full [System Design: Dropbox](docs/system-design.md) to the MVP scope defined in
> [docs/mvp-scope.md](docs/mvp-scope.md). This document is the concrete build contract —
> it specifies every entity, endpoint, and service decision the implementation must follow.
> It contains zero app code; the acceptance suite in `verify/acceptance/` enforces the FRs.

## 1. Architecture Overview

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

    classDef edge  fill:#fff3bf,stroke:#f08c00,color:#1a1a1a;
    classDef svc   fill:#d0ebff,stroke:#1c7ed6,color:#1a1a1a;
    classDef store fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a;

    class R_FILES,R_BLOCKS,R_SHARE,HZ svc
    class FS,BS,SS svc
    class PG,FSYS store
```

**Layers:** Router (HTTP parse/validate/serialize, no business logic) → Service (business logic + data access) → Model (ORM, DB session). This is the standard FastAPI three-layer split from `SYSTEM-DESIGN-MVP-STANDARDS.md`.

**Block storage** uses the local filesystem at `data/blocks/<sha256-hex>`, simulating Magic Pocket. No erasure coding, no cross-zone replication, no cold tiering — those are out of MVP scope.

**No WebSockets, no Kafka, no notification pods.** File listing uses a simple polled `GET /files/list?namespace_id=` endpoint. Out-of-scope for MVP per `docs/mvp-scope.md`.

## 2. Data Model (SQLAlchemy ORM)

```python
# models/file.py
class File(Base):
    __tablename__ = "files"

    file_id:      Mapped[uuid.UUID] = Column(UUID, primary_key=True, default=uuid.uuid4)
    namespace_id: Mapped[int]       = Column(BigInteger, index=True, nullable=False)
    path:         Mapped[str]       = Column(Text, nullable=False)
    blocklist:    Mapped[list[str]] = Column(ARRAY(Text), nullable=False, default=[])
    revision:     Mapped[int]       = Column(Integer, nullable=False, default=1)
    is_deleted:   Mapped[bool]      = Column(Boolean, nullable=False, default=False)
    size:         Mapped[int]       = Column(BigInteger, nullable=False, default=0)
    modified_at:  Mapped[datetime]  = Column(DateTime(timezone=True), server_default=func.now())

    # Unique: one file path per namespace (non-deleted)
    __table_args__ = (
        Index("ix_files_ns_path", "namespace_id", "path", unique=True,
              postgresql_where=text("NOT is_deleted")),
    )
```

```python
# models/block.py
class Block(Base):
    __tablename__ = "blocks"

    block_hash: Mapped[str]     = Column(Text, primary_key=True)   # SHA-256 hex (64 chars)
    size:       Mapped[int]     = Column(BigInteger, nullable=False)  # 4194304 for MVP
    ref_count:  Mapped[int]     = Column(Integer, nullable=False, default=1)
    stored_at:  Mapped[datetime] = Column(DateTime(timezone=True), server_default=func.now())
```

```python
# models/share.py
class Share(Base):
    __tablename__ = "shares"

    share_id:    Mapped[uuid.UUID] = Column(UUID, primary_key=True, default=uuid.uuid4)
    file_id:     Mapped[uuid.UUID] = Column(ForeignKey("files.file_id"), nullable=False)
    owner_id:    Mapped[int]       = Column(BigInteger, nullable=False)
    shared_with: Mapped[int]       = Column(BigInteger, nullable=False)
    access_type: Mapped[str]       = Column(String(20), nullable=False, default="reader")
                                     # enum: reader | editor | viewer
    created_at:  Mapped[datetime]  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("file_id", "shared_with", name="uq_share_file_user"),
    )
```

### Key design decisions for the data model

| Decision | Rationale |
|---|---|
| `blocklist` as `ARRAY(Text)` | PostgreSQL native array avoids a join table for the common case of blocklist retrieval. An ordered list of 64-char hex hashes is compact and well-supported by SQLAlchemy 2.0. |
| `ref_count` on Block, not a join count | Reference counting is the Dropbox production pattern (Magic Pocket GC). Counting via `SELECT COUNT(*)` from a file-block join table would be correct but slower at scale; the MVP is small, but the `ref_count` approach is the one the full design expects, so we ship it now. |
| Partial unique index `ix_files_ns_path` on `NOT is_deleted` | Allows soft-deleted files to leave their path slot occupied so a new file at the same path can reuse it. The full design's SFJ journal tracks path moves; MVP simplifies to this partial unique. |
| `namespace_id` as `bigint`, not UUID | Mimics the full design's entity model (UserEntity namespace). MVP uses mock user headers; namespace_id is the scope key. |
| No `content_hash` column | The full design stores `content_hash = hash(blocklist)` for delta comparison. MVP scope cuts delta sync — blocks are always uploaded in full. |
| `Share.access_type` as `reader | editor | viewer` | Reserved for future MVP expansion. MVP only exercises `reader` per FR-6. |

## 3. API Contracts

All endpoints are mounted on the FastAPI app. Request/response bodies are JSON. Mock auth via `X-User-Id` header (out-of-scope for MVP: no OAuth).

### 3.1 Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe. Returns `200 {"status":"ok"}`. Used by compose healthcheck. |

### 3.2 Files

**`POST /files/commit`** — Upload blocklist, create or update a file revision.

```
Request:
  {
    "namespace_id":  int,
    "path":          str,
    "blocklist":     [str, ...],   // ordered SHA-256 hex hashes
    "parent_revision": int | null  // null for new files, current revision for updates
  }

Response 201:
  {
    "file_id":       "uuid",
    "revision":      int,
    "need_blocks":   [str, ...]    // hashes not yet stored (empty if all blocks exist)
  }

Errors:
  422 — missing required fields (namespace_id, path, blocklist)
  409 — Conflict: parent_revision doesn't match current revision
        Body: {"error": "Conflict", "current_revision": N}
```

Two-phase commit logic:
1. Client sends blocklist → server checks Block table for each hash → returns `need_blocks`
2. Client uploads missing blocks via `POST /blocks/put`
3. Client re-commits same payload → all blocks exist → server atomically creates/updates File row + bumps revision

This is a simplified two-phase commit. In the full design, the Metadata API/Edgestore handles commit + journal write atomically. MVP collapses this into a single `POST /files/commit` call that can be retried after block uploads.

**`GET /files/{file_id}`** — Get file metadata + blocklist.

```
Response 200:
  {
    "file_id":    "uuid",
    "path":       str,
    "blocklist":  [str, ...],
    "revision":   int,
    "size":       int,
    "modified_at": "ISO8601"
  }

Errors:
  404 — file not found or is_deleted = true
```

**`GET /files/{file_id}/metadata`** — Get file metadata only (no blocks in response).

```
Response 200:
  {
    "file_id":     "uuid",
    "path":        str,
    "block_count": int,
    "size":        int,
    "revision":    int,
    "modified_at": "ISO8601",
    "is_deleted":  bool
  }

Errors:
  404 — file not found
```

**`GET /files/list?namespace_id={ns}`** — List non-deleted files in a namespace.

```
Response 200:
  [
    {
      "file_id":    "uuid",
      "path":       str,
      "revision":   int,
      "size":       int,
      "modified_at": "ISO8601"
    },
    ...
  ]

Notes:
  - Empty namespace → 200 []
  - Excludes is_deleted = true files
```

**`POST /files/delete`** — Soft-delete a file.

```
Request:
  {
    "namespace_id":   int,
    "file_id":        "uuid",
    "parent_revision": int
  }

Response 200:
  {
    "file_id":  "uuid",
    "deleted":  true
  }

Errors:
  404 — file not found or already deleted
  409 — Conflict: parent_revision doesn't match current revision
```

On delete: set `is_deleted = true`, then decrement `ref_count` for each block in the file's blocklist. If `ref_count` reaches 0, the block file on disk is not immediately deleted (GC is out of MVP scope), but it's no longer referenced.

### 3.3 Blocks

**`POST /blocks/put`** — Upload a 4 MB block (base64-encoded).

```
Request:
  {
    "block_hash": str,   // SHA-256 hex of the raw 4 MB content
    "data":       str    // base64-encoded 4 MB block bytes
  }

Response 201:
  {
    "block_hash": str,
    "status":     "stored" | "already_exists"
  }

Notes:
  - Idempotent: uploading the same hash twice returns 201 "already_exists";
    no duplicate storage or DB row.
  - Server verifies SHA-256(data) == block_hash; mismatch → 422.
  - Block data stored at data/blocks/<block_hash> as raw bytes.

Errors:
  422 — hash mismatch, invalid base64, or data exceeds 4 MB
```

**`GET /blocks/{block_hash}`** — Download a block by hash.

```
Response 200:
  {
    "block_hash": str,
    "data":       str   // base64-encoded raw block bytes
  }

Errors:
  404 — block not found in storage
```

### 3.4 Sharing

**`POST /sharing/add`** — Share a file with another user.

```
Request:
  {
    "file_id":     "uuid",
    "user_id":     int,     // user to share with
    "access_type": "reader" // only "reader" exercised in MVP
  }

Response 201:
  {
    "share_id":    "uuid",
    "file_id":     "uuid",
    "owner_id":    int,
    "shared_with": int,
    "access_type": "reader"
  }

Errors:
  404 — file not found
  409 — self-share (owner_id == user_id)
  409 — already shared (UNIQUE constraint on file_id, shared_with)
      Body: {"error": "Already shared"}
```

Note: `owner_id` is derived from the `X-User-Id` header on the request. MVP does not store ownership on the File row itself — ownership is implicit in the Share row's `owner_id`. This is a simplification; the full design uses Edgestore's Entity/Association model with folder ownership.

**`GET /sharing/list?user_id={uid}`** — List files shared with a user.

```
Response 200:
  [
    {
      "file_id":     "uuid",
      "path":        str,
      "owner_id":    int,
      "access_type": str
    },
    ...
  ]

Notes:
  - Empty → 200 []
  - Joins shares + files to include the file path.
```

## 4. Service Layer Design

### FileService (`services/file_service.py`)

```
commit(db, namespace_id, path, blocklist, parent_revision) → (File, need_blocks)
  └─ Validate blocklist entries are valid SHA-256 hex (64 chars).
  └─ Look up existing file by (namespace_id, path, is_deleted=false).
  └─ Conflict check: if existing and parent_revision != file.revision → raise ConflictError(current_revision).
  └─ Query Block table: which hashes don't exist? → need_blocks.
  └─ If need_blocks is non-empty → return need_blocks without mutating (phase 1 of two-phase commit).
  └─ If need_blocks is empty:
       └─ If existing file: increment revision, update blocklist, size, modified_at.
       └─ If new file: INSERT with revision=1.
       └─ Increment ref_count for each block in the blocklist.
       └─ Commit transaction.

get_file(db, file_id) → File | None
  └─ SELECT where file_id = X AND is_deleted = false.

get_file_metadata(db, file_id) → dict | None
  └─ SELECT file_id, path, block_count=len(blocklist), size, revision, modified_at, is_deleted.
  └─ Returns metadata even for deleted files (is_deleted=true is legitimate metadata).

list_files(db, namespace_id) → list[File]
  └─ SELECT where namespace_id = X AND is_deleted = false, ordered by path.

delete_file(db, namespace_id, file_id, parent_revision) → None
  └─ Look up file; 404 if not found or already deleted.
  └─ Conflict check: parent_revision must match current revision.
  └─ Set is_deleted = true.
  └─ For each hash in blocklist: UPDATE blocks SET ref_count = ref_count - 1.
  └─ Commit transaction.
```

### BlockService (`services/block_service.py`)

```
store_block(db, block_hash, data_base64) → str ("stored" | "already_exists")
  └─ Decode base64 → raw bytes.
  └─ Verify SHA-256(raw_bytes) == block_hash; mismatch → 422.
  └─ Check Block table for existing hash.
  └─ If exists → return "already_exists" (idempotent).
  └─ Else:
       └─ Write raw bytes to data/blocks/<block_hash>.
       └─ INSERT INTO blocks (block_hash, size, ref_count=1).
       └─ Commit. Return "stored".

get_block(db, block_hash) → (block_hash, data_base64) | None
  └─ Check Block table exists + file exists on disk.
  └─ Read data/blocks/<block_hash> → base64 encode → return.
  └─ Not found in DB or on disk → None (404).

verify_hash(raw_bytes, claimed_hash) → bool
  └─ hashlib.sha256(raw_bytes).hexdigest() == claimed_hash.
```

### SharingService (`services/sharing_service.py`)

```
add_share(db, file_id, owner_id, shared_with, access_type="reader") → Share
  └─ Look up file; 404 if not found.
  └─ If owner_id == shared_with → 409 Conflict (self-share).
  └─ Check UNIQUE(file_id, shared_with); if exists → 409 Conflict.
  └─ INSERT Share row. Return 201.

list_shares(db, user_id) → list[dict]
  └─ SELECT shares JOIN files ON shares.file_id = files.file_id
  └─ WHERE shared_with = user_id.
  └─ Return file_id, path, owner_id, access_type.
```

## 5. Block Storage Design

### Physical layout

```
data/blocks/
├── a1b2c3d4e5f6...   (4 MB raw bytes, filename = SHA-256 hex)
├── f7e8d9c0b1a2...
└── ...
```

- **Content-addressed** by SHA-256 hex digest (64-character lowercase hex string).
- **Fixed block size:** 4 MB = 4,194,304 bytes. MVP does not support variable-size or CDC chunks.
- **Write path:** `block_service.store_block()` writes raw bytes atomically by writing to a temp file then `os.rename()` — this prevents partial reads if the process crashes mid-write.
- **Read path:** `open(data/blocks/<hash>, "rb").read()` → base64 encode.
- **No GC in MVP:** Blocks with `ref_count = 0` remain on disk. The full design's Magic Pocket GC compaction job is out of scope.

### Why local filesystem, not object storage?

The full design uses Magic Pocket (exabyte-scale blob store with cross-zone replication). MVP replaces it with the local filesystem at `data/blocks/` because:
- Zero infrastructure: no S3/MinIO to configure in compose.
- Path is configurable via `Settings.block_storage_dir` (default `data/blocks`).
- The interface (`block_service.store_block` / `get_block`) is the same regardless of backend — swapping to S3 later is a single-file change in `block_service.py`.

### Disk estimate

At 4 MB per block with 250M blocks at exabyte scale (full design), metadata alone is 250M × ~50 bytes = ~12.5 GB in Block Index. MVP will never approach this. The block files themselves are the dominant storage cost.

## 6. Key Decisions & Trade-offs

### D1: Two-phase commit with retry vs. single atomic upload

**Chosen:** Two-phase commit (commit → upload missing blocks → recommit).

**Alternative considered:** Single `POST /files/commit` that includes block data inline (multipart or base64-encoded array). Simpler client logic — one request.

**Pro (chosen):** Matches the full Dropbox design's upload flow. Deduplication happens server-side: the client never uploads a block it already has. When uploading a file that is 90% identical to a previous version, only the new blocks traverse the network.

**Con (chosen):** Requires client-side retry logic (two HTTP round-trips). More complex than a single upload.

**Rationale:** Two-phase commit is the Dropbox production upload flow. The bandwidth savings from deduplication are existential — without it, every file revision re-uploads all blocks. The two-phase approach makes deduplication explicit and testable (FR-1 exercises both phases).

### D2: PostgreSQL ARRAY for blocklist vs. join table

**Chosen:** `blocklist TEXT[]` column on `files`.

**Alternative:** Join table `file_blocks(file_id, block_hash, position)`. Normalized, queryable per-block, standard relational design.

**Pro (chosen):** The blocklist is always fetched and written as an ordered whole — the query pattern is "give me all blocks for this file." An ARRAY avoids a JOIN on every file read and keeps the schema simpler for the MVP.

**Con (chosen):** Can't query "which files contain block hash X?" via SQL — would need `ANY()` array operator or an unnest. MVP doesn't need this query (ref_count tracks it); if needed later, a migration to a join table is straightforward.

**Rationale:** The full design stores blocklists as blobs in SFJ (not relational). The ARRAY is the closest relational analog. An ordered list of hashes is exactly what the download path needs.

### D3: Soft-delete with ref_count decrement vs. hard-delete with cascading GC

**Chosen:** Soft-delete (set `is_deleted = true`, decrement block `ref_count`).

**Alternative:** Hard-delete the File row; run a GC job that finds unreferenced blocks.

**Pro (chosen):** Soft-delete enables undo/restore later. The `ref_count` mechanism is the same one Magic Pocket uses for GC. Decrementing on delete means block storage is always accurate even without a GC sweep.

**Con (chosen):** Blocks with `ref_count = 0` accumulate on disk until a GC job runs. For MVP, this is negligible.

### D4: Revision-based optimistic concurrency vs. last-write-wins

**Chosen:** Optimistic concurrency via `parent_revision`.

**Alternative:** Last-write-wins — no conflict detection. Simpler.

**Pro (chosen):** Prevents silent data loss. If client A and client B both edit the same file, one sees 409 Conflict and creates a conflicted copy (Dropbox's production pattern from FR3 of the full design).

**Rationale:** This is the Dropbox production model. Revision is a monotonically increasing integer per file. The client compares its cached revision to the server's current revision. Stale client → 409 → client renames local file as "filename (conflicted copy).ext" and uploads as a new entry.

### D5: Mock auth (X-User-Id header) vs. no auth

**Chosen:** `X-User-Id` header for user identity.

**Alternative:** No auth at all — every endpoint accepts a `user_id` or `namespace_id` in the body.

**Pro (chosen):** The sharing endpoints need to know who the *caller* is (owner vs. shared-with). The header establishes caller identity without coupling it to the request body. Namespace_id is still explicit in request bodies for file operations.

**Con (chosen):** Not production auth. OAuth / JWT is out of MVP scope.

### D6: Separate `/blocks/put` and `/blocks/{hash}` endpoints vs. inline blocks in `/files/commit`

**Chosen:** Separate block endpoints.

**Alternative:** Multipart upload where block data is included in the commit.

**Pro (chosen):** Blocks are the unit of deduplication and the unit of transfer. Separating them lets the client download individual blocks for file reconstruction (FR-2), upload blocks independently of commits, and enables future block-level operations (LAN Sync, block caching).

### D7: Share ownership derived from X-User-Id vs. stored on File

**Chosen:** `owner_id` on the Share row, derived from `X-User-Id` at share creation time.

**Alternative:** Store `owner_id` on the File row itself.

**Pro (chosen):** A file can have multiple legitimate owners in the future (shared folders). The File row doesn't need to encode ownership; ownership is a relationship between a user and a file, captured in the Share. For MVP, ownership is implicit: the user who first uploads a file is the de facto owner (they hold the `parent_revision`).

**Con (chosen):** No explicit file ownership means anyone who knows the `file_id` could attempt to update it (subject to revision conflict). This is acceptable for MVP with mock auth.

## 7. Module Layout (implementation-ready)

```
src/dropbox/               ← package name: dropbox (importable as dropbox.*)
├── __init__.py
├── main.py                # create_app() factory + lifespan + /healthz
├── config.py              # pydantic-settings: DATABASE_URL, BLOCK_STORAGE_DIR
├── database.py            # SQLAlchemy async engine, get_session dependency
├── models/
│   ├── __init__.py
│   ├── file.py            # File ORM model
│   ├── block.py           # Block ORM model
│   └── share.py           # Share ORM model
├── schemas/
│   ├── __init__.py
│   ├── file.py            # CommitRequest, FileResponse, FileMetadataResponse, etc.
│   ├── block.py           # BlockPutRequest, BlockResponse
│   └── sharing.py         # AddShareRequest, ShareResponse
├── routers/
│   ├── __init__.py
│   ├── files.py           # /files/* endpoints (thin: parse → call service → serialize)
│   ├── blocks.py          # /blocks/* endpoints
│   └── sharing.py         # /sharing/* endpoints
└── services/
    ├── __init__.py
    ├── file_service.py    # commit, get, list, delete, metadata business logic
    ├── block_service.py   # store, retrieve, hash verification, dedup
    └── sharing_service.py # add_share, list_shares, access check

alembic/
├── env.py
├── versions/
│   └── 001_initial.py     # Creates files, blocks, shares tables

tests/
├── conftest.py
├── unit/                  # Isolated unit tests
│   ├── test_file_service.py
│   ├── test_block_service.py
│   └── test_sharing_service.py
└── functional/            # In-process httpx.ASGITransport scenarios
    ├── conftest.py
    ├── test_files.py
    ├── test_blocks.py
    └── test_sharing.py

verify/
├── manifest.env           # e2e-verify configuration
└── acceptance/            # Black-box HTTP contract (one per FR)
    ├── conftest.py
    ├── test_fr1_upload_dedup.py
    ├── test_fr2_download_reconstruct.py
    ├── test_fr3_list_files.py
    ├── test_fr4_soft_delete.py
    ├── test_fr5_conflict_detection.py
    ├── test_fr6_share_file.py
    ├── test_fr7_list_shares.py
    └── test_fr8_file_metadata.py

data/
└── blocks/                # Local filesystem block storage (gitignored)

docs/
├── system-design.md       # Full target design (from system-designs)
├── mvp-scope.md           # MVP contract (from build kickoff)
└── synthesis.md           # Writer's evidence-backed summary (added later)

design.md                  # This file
AGENTS.md                  # Agent workspace rules
KICKOFF.md                 # How to launch the build loop
README.md                  # What it is, stack, quick start, API table
DEPLOY.md                  # Host run/teardown steps
.gitignore
.env.example
pyproject.toml
Dockerfile
docker-compose.yml
```

**Tier assignments for implementation (per the kanban build plan):**

| Task | Tier | Rationale |
|------|------|-----------|
| `models/*.py` (3 ORM models) | staff | Data model + migrations are load-bearing; wrong schema = broken system |
| `services/file_service.py` (commit + conflict + delete logic) | staff | Core algorithm: two-phase commit, revision concurrency, ref_count safety |
| `services/block_service.py` (store + verify + dedup) | staff | Block-level deduplication and hash integrity are performance/security-critical |
| `services/sharing_service.py` | senior | CRUD with FK constraints |
| `routers/*.py` (all 3) | senior | Thin HTTP glue: parse Pydantic, call service, serialize response |
| `schemas/*.py` (all 3) | senior | Pydantic DTOs: field validation, serialization config |
| `config.py` | senior | pydantic-settings boilerplate |
| `database.py` | senior | Engine + session factory |
| `main.py` | senior | App factory + lifespan + healthz |
| `alembic/` migration | staff | Schema DDL must match models exactly |
| `tests/unit/` | senior | Standard unit test scaffolding |
| `tests/functional/` | senior | ASGITransport integration tests |
| `Dockerfile` | senior | Multi-stage build |
| `docker-compose.yml` | sre | Service orchestration |
| `.env.example` | senior | Documentation |
| `pyproject.toml` | senior | Dependency manifest |

## 8. Acceptance Criteria (build-gate checklist)

Each FR maps to exactly one black-box acceptance test in `verify/acceptance/`. All eight must pass against the running system for the build to ship.

| # | FR | Test file | What it proves |
|---|----|-----------|----------------|
| 1 | Upload + dedup | `test_fr1_upload_dedup.py` | Two-phase commit flow; second commit of same blocklist returns empty need_blocks; block count = 3 |
| 2 | Download | `test_fr2_download_reconstruct.py` | GET file returns blocklist; GET each block; reconstructed content matches original |
| 3 | List files | `test_fr3_list_files.py` | 3 files created → 3 listed; 1 deleted → 2 listed; empty namespace → [] |
| 4 | Soft delete | `test_fr4_soft_delete.py` | Delete → file listed as absent; GET /files/{id} → 404; block ref_counts decremented |
| 5 | Conflict | `test_fr5_conflict_detection.py` | Commit with stale parent_revision → 409 with current_revision |
| 6 | Share | `test_fr6_share_file.py` | Share with reader → 201; self-share → 409; reader can GET metadata; reader cannot delete |
| 7 | List shares | `test_fr7_list_shares.py` | Share 2 files → list returns 2; no shares → [] |
| 8 | Metadata | `test_fr8_file_metadata.py` | GET metadata → block_count, size, revision, is_deleted fields correct |

## 9. Run & Test

```bash
# Start the stack (host-only, requires Docker)
docker compose up -d

# Run migrations
docker compose run app alembic upgrade head

# Verify health
curl http://localhost:8000/healthz

# Run white-box tests (in sandbox)
pip install -e ".[dev]"
pytest tests/unit/ tests/functional/ -v

# Run black-box acceptance (against running system)
API_BASE_URL=http://localhost:8000 pytest verify/acceptance/ -v
```
