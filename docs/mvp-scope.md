# Dropbox MVP — Scope & Acceptance Contract

## Stack
- **Language:** Python 3.12
- **Framework:** FastAPI
- **Datastore:** PostgreSQL (files metadata, blocks index, sharing ACLs) + local filesystem (block storage, simulating Magic Pocket)
- **Test runner:** pytest + httpx (ASGITransport for functional, HTTP for black-box acceptance)
- **Container:** Docker Compose (app + postgres)

## Scope IN
- Upload files with fixed 4 MB block-level deduplication (SHA-256)
- Download/reconstruct files from blocklists
- List files in a namespace (user-scoped)
- Soft-delete files
- Conflict detection via revision-based optimistic concurrency (409 Conflict)
- Share files/folders with other users (read access)
- List files shared with a user
- File metadata retrieval (name, size, block count, modified_at)

## Scope OUT
- Real-time WebSocket notifications (use polling for list deltas)
- Full-text search (Nautilus)
- Thumbnail/preview generation (Riviera/Cannes)
- LAN Sync peer discovery
- Erasure coding / cold storage tiering
- Cross-zone replication
- Block Server / Magic Pocket physical layer (local filesystem simulation)
- OAuth / real auth (mock user headers for MVP)

## Functional Requirements

### FR-1 — Upload file with deduplication
Upload a file by providing an ordered blocklist + block data. Server stores only unique blocks (SHA-256 dedup).
- **POST /files/commit** `{namespace_id, path, blocklist: [hash1, ...], parent_revision}` → `201 {file_id, revision, need_blocks: [hash3, hash7]}`
- **POST /blocks/put** `{block_hash, data}` → `201` (idempotent — same hash is a no-op)
- Missing required fields → `422`

### FR-2 — Download / reconstruct file
Retrieve a file's blocklist and download its blocks in order.
- **GET /files/{file_id}** → `200 {file_id, path, blocklist, revision, size, modified_at}`
- **GET /blocks/{block_hash}** → `200 {block_hash, data}` (raw block bytes as base64)
- File not found → `404`

### FR-3 — List files in namespace
List all non-deleted files in a namespace.
- **GET /files/list?namespace_id={ns}** → `200 [{file_id, path, revision, size, modified_at}, ...]`
- Empty namespace → `200 []`

### FR-4 — Soft-delete file
Mark a file as deleted without removing blocks.
- **POST /files/delete** `{namespace_id, file_id, parent_revision}` → `200 {file_id, deleted: true}`
- File already deleted → `404`
- Conflict (stale revision) → `409`

### FR-5 — Conflict detection
Reject commits with stale parent_revision.
- **POST /files/commit** with outdated `parent_revision` → `409 {error: "Conflict", current_revision: N}`
- Concurrent upload with current revision → `201` (both succeed serially)

### FR-6 — Share file/folder
Grant a user read access to a file or folder.
- **POST /sharing/add** `{file_id, user_id, access_type: "reader"}` → `201`
- Share with self → `409`
- File not found → `404`

### FR-7 — List shared files
List files shared with the current user.
- **GET /sharing/list?user_id={uid}** → `200 [{file_id, path, owner_id, access_type}, ...]`
- No shares → `200 []`

### FR-8 — File metadata
Get file details without downloading blocks.
- **GET /files/{file_id}/metadata** → `200 {file_id, path, block_count, size, revision, modified_at, is_deleted}`
- Not found → `404`

## Acceptance Criteria (one executable case per FR)

1. **FR-1 Upload + dedup:** POST /files/commit with 3 blocks → 201 with need_blocks=[all]. POST /blocks/put for each. Re-POST /files/commit with same blocklist → 201 with need_blocks=[]. Block count in DB = 3.
2. **FR-2 Download:** After upload, GET /files/{id} returns blocklist. GET each block. Reconstruct file; content matches original.
3. **FR-3 List:** Create 3 files in namespace. GET /files/list?namespace_id=X → 3 entries. Delete 1; list → 2 entries.
4. **FR-4 Soft-delete:** DELETE a file. GET /files/{id} → 404. GET /files/list → file absent. Block ref_counts decremented.
5. **FR-5 Conflict:** Commit revision 1, then another commit with parent_revision=1 (stale — current is 2) → 409.
6. **FR-6 Share:** User A creates file. User A shares with User B (reader). User B can GET file metadata. User B cannot delete.
7. **FR-7 List shares:** Share 2 files with User B. GET /sharing/list?user_id=B → 2 entries.
8. **FR-8 Metadata:** GET /files/{id}/metadata after upload → block_count=3, size=12MB, is_deleted=false.
