"""FR-8 — File metadata retrieval.

GET /files/{file_id}/metadata → 200 with file details (no block data).
Fields: file_id, path, block_count, size, revision, modified_at, is_deleted.
File not found → 404.
"""

from verify.acceptance.conftest import (
    assert_200,
    assert_404,
    upload_file,
)


def test_metadata_returns_correct_fields(client, fresh_namespace_id):
    """After upload, GET /files/{id}/metadata → all fields present and correct."""
    ns = fresh_namespace_id
    seeds = ["m1", "m2", "m3"]
    body = upload_file(client, ns, "/meta/info.dat", seeds)
    file_id = body["file_id"]

    r = client.get(f"/files/{file_id}/metadata")
    meta = assert_200(r)

    assert meta["file_id"] == file_id
    assert meta["path"] == "/meta/info.dat"
    assert meta["block_count"] == 3
    assert meta["size"] == 3 * 4 * 1024 * 1024  # 12 MB
    assert meta["revision"] == 1
    assert "modified_at" in meta
    assert meta["is_deleted"] is False


def test_metadata_returns_is_deleted_true_after_soft_delete(client, fresh_namespace_id):
    """After soft-delete, metadata endpoint still returns file with is_deleted=true."""
    ns = fresh_namespace_id
    body = upload_file(client, ns, "/meta/deleted.dat", ["md1"])
    file_id = body["file_id"]

    # Delete the file
    r_del = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": file_id,
            "parent_revision": body["revision"],
        },
        headers={"X-User-Id": "1"},
    )
    assert r_del.status_code == 200

    # Metadata should still be accessible and show is_deleted=true
    r = client.get(f"/files/{file_id}/metadata")
    meta = assert_200(r)
    assert meta["is_deleted"] is True
    assert meta["file_id"] == file_id


def test_metadata_not_found_returns_404(client):
    """GET /files/{uuid}/metadata for nonexistent file → 404."""
    r = client.get("/files/00000000-0000-0000-0000-000000000000/metadata")
    assert_404(r)


def test_metadata_single_block_file(client, fresh_namespace_id):
    """File with 1 block → block_count=1, size=4MB."""
    ns = fresh_namespace_id
    body = upload_file(client, ns, "/solo/single.bin", ["solo"])
    file_id = body["file_id"]

    r = client.get(f"/files/{file_id}/metadata")
    meta = assert_200(r)
    assert meta["block_count"] == 1
    assert meta["size"] == 4 * 1024 * 1024
