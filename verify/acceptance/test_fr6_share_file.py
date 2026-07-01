"""FR-6 — Share file/folder with another user.

POST /sharing/add {file_id, user_id, access_type: "reader"} → 201.
Self-share → 409.
File not found → 404.
Shared reader can GET file metadata; cannot delete.
"""

from verify.acceptance.conftest import (
    assert_200,
    assert_201,
    assert_404,
    assert_409,
    upload_file,
)


def test_share_file_with_reader_success(client, fresh_namespace_id, fresh_user_id):
    """Owner shares file with another user → 201, reader can GET metadata."""
    ns = fresh_namespace_id
    owner_id = 100
    reader_id = fresh_user_id

    body = upload_file(client, ns, "/shared/doc.pdf", ["sh1"], user_id=owner_id)
    file_id = body["file_id"]

    # Share with reader
    r = client.post(
        "/sharing/add",
        json={
            "file_id": file_id,
            "user_id": reader_id,
            "access_type": "reader",
        },
        headers={"X-User-Id": str(owner_id)},
    )
    share = assert_201(r)
    assert share["file_id"] == file_id
    assert share["owner_id"] == owner_id
    assert share["shared_with"] == reader_id
    assert share["access_type"] == "reader"

    # Reader can GET file metadata
    r_meta = client.get(
        f"/files/{file_id}/metadata",
        headers={"X-User-Id": str(reader_id)},
    )
    assert_200(r_meta)


def test_self_share_returns_409(client, fresh_namespace_id):
    """Sharing with self → 409 Conflict."""
    ns = fresh_namespace_id
    user_id = 200

    body = upload_file(client, ns, "/self/doc.txt", ["sf1"], user_id=user_id)

    r = client.post(
        "/sharing/add",
        json={
            "file_id": body["file_id"],
            "user_id": user_id,  # same as owner
            "access_type": "reader",
        },
        headers={"X-User-Id": str(user_id)},
    )
    assert_409(r)


def test_share_nonexistent_file_returns_404(client, fresh_user_id):
    """Share a file that doesn't exist → 404."""
    r = client.post(
        "/sharing/add",
        json={
            "file_id": "00000000-0000-0000-0000-000000000000",
            "user_id": fresh_user_id,
            "access_type": "reader",
        },
        headers={"X-User-Id": "999"},
    )
    assert_404(r)


def test_reader_cannot_delete_file(client, fresh_namespace_id, fresh_user_id):
    """Shared reader attempts delete → should be rejected (403 or 404 from their perspective)."""
    ns = fresh_namespace_id
    owner_id = 300
    reader_id = fresh_user_id

    body = upload_file(client, ns, "/protected/secret.txt", ["pr1"], user_id=owner_id)
    file_id = body["file_id"]

    # Share with reader
    client.post(
        "/sharing/add",
        json={
            "file_id": file_id,
            "user_id": reader_id,
            "access_type": "reader",
        },
        headers={"X-User-Id": str(owner_id)},
    )

    # Reader tries to delete
    r = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": file_id,
            "parent_revision": body["revision"],
        },
        headers={"X-User-Id": str(reader_id)},
    )
    # Reader should be forbidden (403) or file not found (404) if access denies visibility.
    # The acceptance test allows either; the requirement is "cannot delete."
    assert r.status_code in (
        403,
        404,
    ), f"Expected 403 or 404 for reader delete, got {r.status_code}: {r.text}"
