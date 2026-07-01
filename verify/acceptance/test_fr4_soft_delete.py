"""FR-4 — Soft-delete file.

POST /files/delete {namespace_id, file_id, parent_revision} → 200.
File already deleted → 404.
Conflict (stale revision) → 409.
After delete: file excluded from list, GET /files/{id} → 404.
Block ref_counts decremented.
"""

from verify.acceptance.conftest import (
    assert_200,
    assert_404,
    assert_409,
    upload_file,
)


def test_soft_delete_file_success(client, fresh_namespace_id):
    """Delete a file → 200, then GET returns 404, list excludes it."""
    ns = fresh_namespace_id
    body = upload_file(client, ns, "/tmp/garbage.txt", ["del1"])
    file_id = body["file_id"]
    revision = body["revision"]

    r = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": file_id,
            "parent_revision": revision,
        },
        headers={"X-User-Id": "1"},
    )
    assert_200(r)

    # File should no longer be accessible
    r_get = client.get(f"/files/{file_id}")
    assert_404(r_get)

    # File should not appear in listing
    r_list = client.get("/files/list", params={"namespace_id": ns})
    files = assert_200(r_list)
    paths = {f["path"] for f in files}
    assert "/tmp/garbage.txt" not in paths


def test_delete_already_deleted_returns_404(client, fresh_namespace_id):
    """Delete a file twice → second attempt returns 404."""
    ns = fresh_namespace_id
    body = upload_file(client, ns, "/twice.txt", ["tw1"])

    # First delete
    r1 = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": body["file_id"],
            "parent_revision": body["revision"],
        },
        headers={"X-User-Id": "1"},
    )
    assert r1.status_code == 200

    # Second delete — already deleted
    r2 = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": body["file_id"],
            "parent_revision": body["revision"],
        },
        headers={"X-User-Id": "1"},
    )
    assert_404(r2)


def test_delete_with_stale_revision_returns_409(client, fresh_namespace_id):
    """Delete with stale parent_revision → 409 Conflict."""
    ns = fresh_namespace_id
    body = upload_file(client, ns, "/conflict-del.txt", ["cd1"])
    file_id = body["file_id"]
    original_rev = body["revision"]

    # Update the file (bumps revision)
    upload_file(client, ns, "/conflict-del.txt", ["cd2"])

    # Try delete with original (stale) revision
    r = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": file_id,
            "parent_revision": original_rev,
        },
        headers={"X-User-Id": "1"},
    )
    err = assert_409(r)
    assert err.get("error") == "Conflict"
    assert "current_revision" in err
    assert err["current_revision"] > original_rev


def test_delete_nonexistent_file_returns_404(client, fresh_namespace_id):
    """Delete a file that never existed → 404."""
    r = client.post(
        "/files/delete",
        json={
            "namespace_id": fresh_namespace_id,
            "file_id": "00000000-0000-0000-0000-000000000000",
            "parent_revision": 1,
        },
        headers={"X-User-Id": "1"},
    )
    assert_404(r)
