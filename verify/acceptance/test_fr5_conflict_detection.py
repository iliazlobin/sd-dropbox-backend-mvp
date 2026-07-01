"""FR-5 — Conflict detection via revision-based optimistic concurrency.

POST /files/commit with stale parent_revision → 409 {error: "Conflict", current_revision: N}.
Concurrent commits with correct revision → both succeed serially.
"""

from verify.acceptance.conftest import (
    assert_201,
    assert_409,
    make_block_hash,
    upload_file,
)


def test_stale_revision_returns_409(client, fresh_namespace_id):
    """Commit revision 1 succeeds. Commit with parent_revision=1 (stale, now is 2) → 409."""
    ns = fresh_namespace_id
    seeds_v1 = ["v1a"]
    [make_block_hash(s) for s in seeds_v1]

    # First commit creates file at revision 1
    body1 = upload_file(client, ns, "/conflict/file.txt", seeds_v1)
    body1["file_id"]
    assert body1["revision"] == 1

    # Update the file (bumps to revision 2)
    seeds_v2 = ["v2a"]
    blocklist_v2 = [make_block_hash(s) for s in seeds_v2]
    r2 = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/conflict/file.txt",
            "blocklist": blocklist_v2,
            "parent_revision": 1,
        },
        headers={"X-User-Id": "1"},
    )
    body2 = assert_201(r2)
    assert body2["revision"] == 2

    # Now try to commit with stale parent_revision=1
    r3 = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/conflict/file.txt",
            "blocklist": blocklist_v2,
            "parent_revision": 1,  # stale!
        },
        headers={"X-User-Id": "1"},
    )
    err = assert_409(r3)
    assert err["error"] == "Conflict"
    assert err["current_revision"] == 2


def test_concurrent_commits_with_correct_revision_succeed(client, fresh_namespace_id):
    """Two sequential commits with correct parent_revision → both succeed."""
    ns = fresh_namespace_id

    # First commit
    seeds_a = ["ca1"]
    body1 = upload_file(client, ns, "/concurrent/task.txt", seeds_a)
    assert body1["revision"] == 1

    # Second commit with correct parent_revision
    seeds_b = ["cb1"]
    r = client.post(
        "/files/commit",
        json={
            "namespace_id": ns,
            "path": "/concurrent/task.txt",
            "blocklist": [make_block_hash(s) for s in seeds_b],
            "parent_revision": 1,  # correct — matches current
        },
        headers={"X-User-Id": "1"},
    )
    body2 = assert_201(r)
    assert body2["revision"] == 2
    assert body2["need_blocks"] == []  # blocks uploaded by upload_file helper (seeds_b are new though)

    # Third commit with correct parent_revision
    seeds_c = ["cc1"]
    assert_201(
        client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/concurrent/task.txt",
                "blocklist": [make_block_hash(s) for s in seeds_c],
                "parent_revision": 2,  # correct
            },
            headers={"X-User-Id": "1"},
        )
    )


def test_new_file_with_parent_revision_null_succeeds(client, fresh_namespace_id):
    """New file commit with parent_revision=null → 201."""
    ns = fresh_namespace_id
    seeds = ["newbie"]
    body = upload_file(client, ns, "/fresh/start.txt", seeds)
    assert "file_id" in body
    assert body["revision"] == 1
