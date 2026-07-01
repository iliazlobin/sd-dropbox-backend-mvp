"""FR-3 — List files in a namespace.

GET /files/list?namespace_id={ns} → 200 with array of file entries.
Empty namespace → 200 [].
Deleted files excluded from list.
Cross-namespace isolation: namespace A files not visible in namespace B.
"""

from verify.acceptance.conftest import (
    assert_200,
    upload_file,
)


def test_list_files_in_namespace(client, fresh_namespace_id):
    """Create 3 files → list returns 3 entries."""
    ns = fresh_namespace_id
    upload_file(client, ns, "/proj/a.txt", ["la1"])
    upload_file(client, ns, "/proj/b.txt", ["lb1"])
    upload_file(client, ns, "/proj/c.txt", ["lc1"])

    r = client.get("/files/list", params={"namespace_id": ns})
    files = assert_200(r)

    assert isinstance(files, list)
    assert len(files) == 3, f"Expected 3 files, got {len(files)}: {files}"
    paths = {f["path"] for f in files}
    assert paths == {"/proj/a.txt", "/proj/b.txt", "/proj/c.txt"}

    # Each entry should have required fields
    for f in files:
        assert "file_id" in f
        assert "revision" in f
        assert "size" in f
        assert "modified_at" in f


def test_list_empty_namespace_returns_empty(client, fresh_namespace_id):
    """Namespace with no files → 200 []."""
    r = client.get("/files/list", params={"namespace_id": fresh_namespace_id})
    files = assert_200(r)
    assert files == []


def test_deleted_file_excluded_from_list(client, fresh_namespace_id):
    """Upload 2 files, delete 1 → list returns only 1 entry."""
    ns = fresh_namespace_id

    body = upload_file(client, ns, "/keep.txt", ["keep1"])
    upload_file(client, ns, "/remove.txt", ["rem1"])

    # Delete /remove.txt
    body["file_id"]
    # Find the remove file's id by listing
    r_list = client.get("/files/list", params={"namespace_id": ns})
    files_before = assert_200(r_list)
    remove_file = [f for f in files_before if f["path"] == "/remove.txt"][0]

    r_del = client.post(
        "/files/delete",
        json={
            "namespace_id": ns,
            "file_id": remove_file["file_id"],
            "parent_revision": remove_file["revision"],
        },
        headers={"X-User-Id": "1"},
    )
    assert r_del.status_code == 200

    # List again
    r2 = client.get("/files/list", params={"namespace_id": ns})
    files_after = assert_200(r2)
    assert len(files_after) == 1
    assert files_after[0]["path"] == "/keep.txt"


def test_cross_namespace_isolation(client, fresh_namespace_id):
    """Files in namespace A not visible in namespace B listing."""
    ns_a = fresh_namespace_id
    ns_b = fresh_namespace_id + 1  # ensure different

    upload_file(client, ns_a, "/secret/a.dat", ["iso1"])
    upload_file(client, ns_b, "/secret/b.dat", ["iso2"])

    r_a = client.get("/files/list", params={"namespace_id": ns_a})
    files_a = assert_200(r_a)
    assert len(files_a) == 1
    assert files_a[0]["path"] == "/secret/a.dat"

    r_b = client.get("/files/list", params={"namespace_id": ns_b})
    files_b = assert_200(r_b)
    assert len(files_b) == 1
    assert files_b[0]["path"] == "/secret/b.dat"
