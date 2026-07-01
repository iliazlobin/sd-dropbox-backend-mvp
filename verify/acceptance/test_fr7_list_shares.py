"""FR-7 — List files shared with a user.

GET /sharing/list?user_id={uid} → 200 with array of shared file entries.
No shares → 200 [].
Each entry includes file_id, path, owner_id, access_type.
"""

from verify.acceptance.conftest import (
    assert_200,
    assert_201,
    upload_file,
)


def test_list_shares_returns_shared_files(client, fresh_namespace_id, fresh_user_id):
    """Share 2 files with user B → list returns 2 entries."""
    ns = fresh_namespace_id
    owner_id = 400
    reader_id = fresh_user_id

    f1 = upload_file(client, ns, "/shared/alpha.txt", ["ls1"], user_id=owner_id)
    f2 = upload_file(client, ns, "/shared/beta.txt", ["ls2"], user_id=owner_id)

    # Share both files
    for f in (f1, f2):
        r = client.post(
            "/sharing/add",
            json={
                "file_id": f["file_id"],
                "user_id": reader_id,
                "access_type": "reader",
            },
            headers={"X-User-Id": str(owner_id)},
        )
        assert_201(r)

    # List shares for reader
    r = client.get("/sharing/list", params={"user_id": reader_id})
    shares = assert_200(r)

    assert isinstance(shares, list)
    assert len(shares) == 2, f"Expected 2 shares, got {len(shares)}: {shares}"

    share_paths = {s["path"] for s in shares}
    assert share_paths == {"/shared/alpha.txt", "/shared/beta.txt"}

    for s in shares:
        assert s["owner_id"] == owner_id
        assert s["access_type"] == "reader"
        assert "file_id" in s


def test_no_shares_returns_empty_list(client, fresh_user_id):
    """User with no shares → 200 []."""
    r = client.get("/sharing/list", params={"user_id": fresh_user_id})
    shares = assert_200(r)
    assert shares == []


def test_list_shares_only_returns_own_shares(client, fresh_namespace_id, fresh_user_id):
    """User A's shares are not visible in User B's list."""
    ns = fresh_namespace_id
    owner = 500
    reader_a = fresh_user_id
    reader_b = fresh_user_id + 1

    f = upload_file(client, ns, "/exclusive/mine.txt", ["ex1"], user_id=owner)

    # Share only with reader A
    client.post(
        "/sharing/add",
        json={
            "file_id": f["file_id"],
            "user_id": reader_a,
            "access_type": "reader",
        },
        headers={"X-User-Id": str(owner)},
    )

    # Reader A sees it
    r_a = client.get("/sharing/list", params={"user_id": reader_a})
    shares_a = assert_200(r_a)
    assert len(shares_a) == 1

    # Reader B does not
    r_b = client.get("/sharing/list", params={"user_id": reader_b})
    shares_b = assert_200(r_b)
    assert len(shares_b) == 0
