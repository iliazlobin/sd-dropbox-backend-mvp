"""FR-1 — Upload file with block-level deduplication.

Two-phase commit: POST /files/commit with blocklist → 201 with need_blocks.
Upload missing blocks via POST /blocks/put (idempotent).
Recommit → 201 with empty need_blocks.
Same blocklist committed twice → second commit deduplicates (empty need_blocks).
"""

from verify.acceptance.conftest import (
    assert_201,
    assert_422,
    make_block_b64,
    make_block_hash,
    upload_file,
)


def test_two_phase_upload_creates_file(client, fresh_namespace_id):
    """Commit blocklist → get need_blocks → upload blocks → recommit → 201."""
    ns = fresh_namespace_id
    seeds = ["alpha", "beta", "gamma"]
    blocklist = [make_block_hash(s) for s in seeds]

    # Phase 1: initial commit — blocks don't exist yet
    r1 = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/docs/report.txt",
            "blocklist": blocklist,
            "parent_revision": None,
        },
        headers={"X-User-Id": "1"},
    )
    body1 = assert_201(r1)
    assert "file_id" in body1
    assert body1["revision"] == 1
    # All blocks are new → all 3 should be in need_blocks
    assert sorted(body1["need_blocks"]) == sorted(
        blocklist
    ), f"Expected all blocks in need_blocks, got {body1['need_blocks']}"

    # Phase 2: upload blocks
    for seed in seeds:
        h = make_block_hash(seed)
        r = client.post(
            "/blocks/put",
            json={"block_hash": h, "data": make_block_b64(seed)},
        )
        body = assert_201(r)
        assert body["status"] in ("stored", "already_exists")

    # Phase 3: recommit — all blocks now exist
    r3 = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/docs/report.txt",
            "blocklist": blocklist,
            "parent_revision": None,
        },
        headers={"X-User-Id": "1"},
    )
    body3 = assert_201(r3)
    assert body3["revision"] == 1
    assert (
        body3["need_blocks"] == []
    ), f"Expected empty need_blocks on recommit, got {body3['need_blocks']}"


def test_dedup_same_blocklist_returns_empty_need_blocks(client, fresh_namespace_id):
    """Upload a file, then commit same blocklist at a different path → empty need_blocks."""
    ns = fresh_namespace_id
    seeds = ["x1", "x2", "x3"]

    # Upload first file (this also uploads the blocks)
    upload_file(client, ns, "/a/file1.bin", seeds)

    # Commit same blocklist at a different path — blocks already stored
    blocklist = [make_block_hash(s) for s in seeds]
    r = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/a/file2.bin",
            "blocklist": blocklist,
            "parent_revision": None,
        },
        headers={"X-User-Id": "1"},
    )
    body = assert_201(r)
    assert (
        body["need_blocks"] == []
    ), f"Dedup failed: expected empty need_blocks, got {body['need_blocks']}"


def test_idempotent_block_put(client):
    """PUT the same block twice → both return 201, second says 'already_exists'."""
    seed = "idempotent-test"
    h = make_block_hash(seed)
    data = make_block_b64(seed)

    r1 = client.post("/blocks/put", json={"block_hash": h, "data": data})
    b1 = assert_201(r1)
    assert b1["status"] == "stored"

    r2 = client.post("/blocks/put", json={"block_hash": h, "data": data})
    b2 = assert_201(r2)
    assert b2["status"] == "already_exists"


def test_commit_missing_fields_returns_422(client, fresh_namespace_id):
    """POST /files/commit without namespace_id → 422."""
    r = client.post(
        "/files/commit",
        json={
            "path": "/test.txt",
            "blocklist": [make_block_hash("zzz")],
            "parent_revision": None,
        },
        headers={"X-User-Id": "1"},
    )
    assert_422(r)


def test_block_put_invalid_hash_returns_422(client):
    """POST /blocks/put with mismatched hash → 422."""
    seed_a = "hash-mismatch-a"
    seed_b = "hash-mismatch-b"
    h = make_block_hash(seed_a)  # claim hash A
    data = make_block_b64(seed_b)  # send data B

    r = client.post("/blocks/put", json={"block_hash": h, "data": data})
    assert_422(r)
