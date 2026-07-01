"""FR-2 — Download file and reconstruct from blocks.

GET /files/{file_id} → 200 with blocklist.
GET /blocks/{block_hash} → 200 with base64 data.
Reconstruct file from ordered blocks; content matches original.
File not found → 404.
"""

from verify.acceptance.conftest import (
    assert_200,
    assert_404,
    make_block_data,
    make_block_hash,
    upload_file,
)


def test_download_file_returns_blocklist(client, fresh_namespace_id):
    """After upload, GET /files/{id} returns metadata + full blocklist."""
    ns = fresh_namespace_id
    seeds = ["dl-a", "dl-b", "dl-c"]
    body = upload_file(client, ns, "/downloads/test.dat", seeds)

    file_id = body["file_id"]
    r = client.get(f"/files/{file_id}")
    data = assert_200(r)

    assert data["file_id"] == file_id
    assert data["path"] == "/downloads/test.dat"
    assert data["revision"] == 1
    assert data["size"] == 3 * 4 * 1024 * 1024  # 12 MB
    expected_blocklist = [make_block_hash(s) for s in seeds]
    assert data["blocklist"] == expected_blocklist


def test_download_blocks_and_reconstruct(client, fresh_namespace_id):
    """Fetch each block and reconstruct; verify content matches original."""
    ns = fresh_namespace_id
    seeds = ["rec-a", "rec-b"]

    upload_file(client, ns, "/reconstruct/file.bin", seeds)
    # The upload_file helper uploaded blocks and committed; blocks exist in storage.

    # Fetch each block
    reconstructed = b""
    for seed in seeds:
        h = make_block_hash(seed)
        r = client.get(f"/blocks/{h}")
        block_data = assert_200(r)
        assert block_data["block_hash"] == h
        # Decode base64 data
        import base64
        reconstructed += base64.b64decode(block_data["data"])

    # Build expected
    expected = make_block_data("rec-a") + make_block_data("rec-b")
    assert reconstructed == expected, \
        f"Reconstructed {len(reconstructed)} bytes doesn't match expected {len(expected)} bytes"


def test_get_file_not_found_returns_404(client):
    """GET /files/{nonexistent-uuid} → 404."""
    r = client.get("/files/00000000-0000-0000-0000-000000000000")
    assert_404(r)


def test_get_block_not_found_returns_404(client):
    """GET /blocks/{nonexistent-hash} → 404."""
    fake_hash = "a" * 64
    r = client.get(f"/blocks/{fake_hash}")
    assert_404(r)
