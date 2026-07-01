"""Functional tests — in-process endpoint scenarios from SPEC.md §6."""

import base64
import hashlib

import pytest

BLOCK_SIZE = 4 * 1024 * 1024


def make_block_data(seed: str) -> bytes:
    h = hashlib.sha256(seed.encode()).digest()
    repeats = (BLOCK_SIZE // len(h)) + 1
    return (h * repeats)[:BLOCK_SIZE]


def make_block_hash(seed: str) -> str:
    return hashlib.sha256(make_block_data(seed)).hexdigest()


def make_block_b64(seed: str) -> str:
    return base64.b64encode(make_block_data(seed)).decode("ascii")


async def upload_file(client, namespace_id, path, seeds, user_id=1):
    """Full two-phase upload helper."""
    blocklist = [make_block_hash(s) for s in seeds]
    r = await client.post(
        "/files/commit",
        json={
            "namespace_id": namespace_id,
            "path": path,
            "blocklist": blocklist,
            "parent_revision": None,
        },
        headers={"X-User-Id": str(user_id)},
    )
    assert r.status_code == 201, f"Phase 1 failed: {r.status_code} {r.text}"
    body = r.json()
    need_blocks = body.get("need_blocks", [])

    for seed in seeds:
        h = make_block_hash(seed)
        if h in need_blocks:
            r2 = await client.post(
                "/blocks/put",
                json={"block_hash": h, "data": make_block_b64(seed)},
            )
            assert r2.status_code == 201

    if need_blocks:
        r3 = await client.post(
            "/files/commit",
            json={
                "namespace_id": namespace_id,
                "path": path,
                "blocklist": blocklist,
                "parent_revision": None,
            },
            headers={"X-User-Id": str(user_id)},
        )
        assert r3.status_code == 201
        return r3.json()
    return body


@pytest.mark.asyncio
class TestFR1UploadDedup:
    """FR-1: two-phase upload, dedup, idempotent block put, validation."""

    async def test_two_phase_upload(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds = ["alpha", "beta", "gamma"]
        blocklist = [make_block_hash(s) for s in seeds]

        r = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/docs/r.txt",
                "blocklist": blocklist,
                "parent_revision": None,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["revision"] == 1
        assert sorted(body["need_blocks"]) == sorted(blocklist)

        # Upload blocks
        for seed in seeds:
            h = make_block_hash(seed)
            rb = await client.post(
                "/blocks/put",
                json={
                    "block_hash": h,
                    "data": make_block_b64(seed),
                },
            )
            assert rb.status_code == 201

        # Recommit
        r3 = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/docs/r.txt",
                "blocklist": blocklist,
                "parent_revision": None,
            },
            headers={"X-User-Id": "1"},
        )
        assert r3.status_code == 201
        body3 = r3.json()
        assert body3["revision"] == 1
        assert body3["need_blocks"] == []

    async def test_dedup_same_blocklist(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds = ["x1", "x2", "x3"]
        await upload_file(client, ns, "/a/f1.bin", seeds)

        blocklist = [make_block_hash(s) for s in seeds]
        r = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/a/f2.bin",
                "blocklist": blocklist,
                "parent_revision": None,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 201
        assert r.json()["need_blocks"] == []

    async def test_idempotent_block_put(self, client):
        seed = "idem-test"
        h = make_block_hash(seed)
        data = make_block_b64(seed)

        r1 = await client.post("/blocks/put", json={"block_hash": h, "data": data})
        assert r1.status_code == 201
        assert r1.json()["status"] == "stored"

        r2 = await client.post("/blocks/put", json={"block_hash": h, "data": data})
        assert r2.status_code == 201
        assert r2.json()["status"] == "already_exists"

    async def test_missing_fields_422(self, client, fresh_namespace_id):
        r = await client.post(
            "/files/commit",
            json={
                "path": "/t.txt",
                "blocklist": [make_block_hash("z")],
                "parent_revision": None,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 422

    async def test_block_hash_mismatch_422(self, client):
        h = make_block_hash("hash-mismatch-a")
        data = make_block_b64("hash-mismatch-b")
        r = await client.post("/blocks/put", json={"block_hash": h, "data": data})
        assert r.status_code == 422


@pytest.mark.asyncio
class TestFR2Download:
    """FR-2: download and reconstruct."""

    async def test_download_returns_blocklist(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds = ["da", "db", "dc"]
        body = await upload_file(client, ns, "/dl/test.dat", seeds)

        r = await client.get(f"/files/{body['file_id']}")
        assert r.status_code == 200
        data = r.json()
        assert data["blocklist"] == [make_block_hash(s) for s in seeds]
        assert data["size"] == 3 * BLOCK_SIZE

    async def test_download_and_reconstruct(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds = ["ra", "rb"]
        await upload_file(client, ns, "/rec/f.bin", seeds)

        reconstructed = b""
        for seed in seeds:
            h = make_block_hash(seed)
            r = await client.get(f"/blocks/{h}")
            assert r.status_code == 200
            reconstructed += base64.b64decode(r.json()["data"])

        expected = make_block_data("ra") + make_block_data("rb")
        assert reconstructed == expected

    async def test_file_not_found_404(self, client):
        r = await client.get("/files/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    async def test_block_not_found_404(self, client):
        r = await client.get("/blocks/" + "a" * 64)
        assert r.status_code == 404


@pytest.mark.asyncio
class TestFR3ListFiles:
    """FR-3: namespace-scoped listing."""

    async def test_list_returns_files(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        await upload_file(client, ns, "/p/a.txt", ["la1"])
        await upload_file(client, ns, "/p/b.txt", ["lb1"])
        await upload_file(client, ns, "/p/c.txt", ["lc1"])

        r = await client.get("/files/list", params={"namespace_id": ns})
        assert r.status_code == 200
        files = r.json()
        assert len(files) == 3
        paths = {f["path"] for f in files}
        assert paths == {"/p/a.txt", "/p/b.txt", "/p/c.txt"}

    async def test_empty_namespace(self, client, fresh_namespace_id):
        r = await client.get("/files/list", params={"namespace_id": fresh_namespace_id})
        assert r.status_code == 200
        assert r.json() == []

    async def test_deleted_excluded(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        await upload_file(client, ns, "/keep.txt", ["k1"])
        await upload_file(client, ns, "/remove.txt", ["r1"])

        # Find remove file id
        rl = await client.get("/files/list", params={"namespace_id": ns})
        files = rl.json()
        remove_f = [f for f in files if f["path"] == "/remove.txt"][0]

        # Delete
        rd = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": remove_f["file_id"],
                "parent_revision": remove_f["revision"],
            },
            headers={"X-User-Id": "1"},
        )
        assert rd.status_code == 200

        # List again
        rl2 = await client.get("/files/list", params={"namespace_id": ns})
        files2 = rl2.json()
        assert len(files2) == 1
        assert files2[0]["path"] == "/keep.txt"

    async def test_cross_namespace_isolation(self, client, fresh_namespace_id):
        ns_a = fresh_namespace_id
        ns_b = fresh_namespace_id + 1
        await upload_file(client, ns_a, "/secret/a.dat", ["iso1"])
        await upload_file(client, ns_b, "/secret/b.dat", ["iso2"])

        ra = await client.get("/files/list", params={"namespace_id": ns_a})
        assert len(ra.json()) == 1
        assert ra.json()[0]["path"] == "/secret/a.dat"

        rb = await client.get("/files/list", params={"namespace_id": ns_b})
        assert len(rb.json()) == 1
        assert rb.json()[0]["path"] == "/secret/b.dat"


@pytest.mark.asyncio
class TestFR4SoftDelete:
    """FR-4: soft-delete."""

    async def test_soft_delete_success(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body = await upload_file(client, ns, "/tmp/g.txt", ["del1"])
        fid = body["file_id"]
        rev = body["revision"]

        r = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": fid,
                "parent_revision": rev,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 200

        # File should be inaccessible
        rg = await client.get(f"/files/{fid}")
        assert rg.status_code == 404

        # File excluded from list
        rl = await client.get("/files/list", params={"namespace_id": ns})
        paths = {f["path"] for f in rl.json()}
        assert "/tmp/g.txt" not in paths

    async def test_delete_already_deleted_404(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body = await upload_file(client, ns, "/tw.txt", ["tw1"])

        r1 = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": body["file_id"],
                "parent_revision": body["revision"],
            },
            headers={"X-User-Id": "1"},
        )
        assert r1.status_code == 200

        r2 = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": body["file_id"],
                "parent_revision": body["revision"],
            },
            headers={"X-User-Id": "1"},
        )
        assert r2.status_code == 404

    async def test_delete_stale_revision_409(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body = await upload_file(client, ns, "/cf-del.txt", ["cd1"])
        original_rev = body["revision"]

        # Update the file
        await upload_file(client, ns, "/cf-del.txt", ["cd2"])

        r = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": body["file_id"],
                "parent_revision": original_rev,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 409
        err = r.json()
        assert err["error"] == "Conflict"
        assert err["current_revision"] > original_rev

    async def test_delete_nonexistent_404(self, client, fresh_namespace_id):
        r = await client.post(
            "/files/delete",
            json={
                "namespace_id": fresh_namespace_id,
                "file_id": "00000000-0000-0000-0000-000000000000",
                "parent_revision": 1,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
class TestFR5ConflictDetection:
    """FR-5: revision-based conflict detection."""

    async def test_stale_revision_409(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds_v1 = ["v1a"]
        body = await upload_file(client, ns, "/conf/f.txt", seeds_v1)
        assert body["revision"] == 1

        # Update to rev 2
        seeds_v2 = ["v2a"]
        r2 = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/conf/f.txt",
                "blocklist": [make_block_hash(s) for s in seeds_v2],
                "parent_revision": 1,
            },
            headers={"X-User-Id": "1"},
        )
        body2 = r2.json()
        assert r2.status_code == 201
        assert body2["revision"] == 2

        # Stale commit
        r3 = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/conf/f.txt",
                "blocklist": [make_block_hash(s) for s in seeds_v2],
                "parent_revision": 1,
            },
            headers={"X-User-Id": "1"},
        )
        assert r3.status_code == 409
        err = r3.json()
        assert err["error"] == "Conflict"
        assert err["current_revision"] == 2

    async def test_concurrent_commits_succeed(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body1 = await upload_file(client, ns, "/conc/t.txt", ["ca1"])
        assert body1["revision"] == 1

        seeds_b = ["cb1"]
        r = await client.post(
            "/files/commit",
            json={
                "namespace_id": ns,
                "path": "/conc/t.txt",
                "blocklist": [make_block_hash(s) for s in seeds_b],
                "parent_revision": 1,
            },
            headers={"X-User-Id": "1"},
        )
        assert r.status_code == 201
        body2 = r.json()
        assert body2["revision"] == 2
        assert body2["need_blocks"] == []


@pytest.mark.asyncio
class TestFR6ShareFile:
    """FR-6: file sharing."""

    async def test_share_file_success(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        owner_id = 100
        reader_id = 201

        body = await upload_file(client, ns, "/sh/doc.pdf", ["sh1"], user_id=owner_id)

        r = await client.post(
            "/sharing/add",
            json={
                "file_id": body["file_id"],
                "user_id": reader_id,
                "access_type": "reader",
            },
            headers={"X-User-Id": str(owner_id)},
        )
        assert r.status_code == 201
        share = r.json()
        assert share["owner_id"] == owner_id
        assert share["shared_with"] == reader_id

        # Reader can GET metadata
        rm = await client.get(
            f"/files/{body['file_id']}/metadata", headers={"X-User-Id": str(reader_id)}
        )
        assert rm.status_code == 200

    async def test_self_share_409(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        uid = 200
        body = await upload_file(client, ns, "/self/doc.txt", ["sf1"], user_id=uid)

        r = await client.post(
            "/sharing/add",
            json={
                "file_id": body["file_id"],
                "user_id": uid,
                "access_type": "reader",
            },
            headers={"X-User-Id": str(uid)},
        )
        assert r.status_code == 409

    async def test_share_nonexistent_404(self, client):
        r = await client.post(
            "/sharing/add",
            json={
                "file_id": "00000000-0000-0000-0000-000000000000",
                "user_id": 777,
                "access_type": "reader",
            },
            headers={"X-User-Id": "999"},
        )
        assert r.status_code == 404

    async def test_reader_cannot_delete(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        owner_id = 300
        reader_id = 301

        body = await upload_file(client, ns, "/prot/sec.txt", ["pr1"], user_id=owner_id)

        await client.post(
            "/sharing/add",
            json={
                "file_id": body["file_id"],
                "user_id": reader_id,
                "access_type": "reader",
            },
            headers={"X-User-Id": str(owner_id)},
        )

        r = await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": body["file_id"],
                "parent_revision": body["revision"],
            },
            headers={"X-User-Id": str(reader_id)},
        )
        assert r.status_code in (403, 404)


@pytest.mark.asyncio
class TestFR7ListShares:
    """FR-7: list shared files."""

    async def test_list_shares(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        owner_id = 400
        reader_id = 401

        f1 = await upload_file(client, ns, "/sh/a.txt", ["ls1"], user_id=owner_id)
        f2 = await upload_file(client, ns, "/sh/b.txt", ["ls2"], user_id=owner_id)

        for f in (f1, f2):
            r = await client.post(
                "/sharing/add",
                json={
                    "file_id": f["file_id"],
                    "user_id": reader_id,
                    "access_type": "reader",
                },
                headers={"X-User-Id": str(owner_id)},
            )
            assert r.status_code == 201

        r = await client.get("/sharing/list", params={"user_id": reader_id})
        assert r.status_code == 200
        shares = r.json()
        assert len(shares) == 2
        paths = {s["path"] for s in shares}
        assert paths == {"/sh/a.txt", "/sh/b.txt"}

    async def test_no_shares_empty(self, client):
        r = await client.get("/sharing/list", params={"user_id": 99999})
        assert r.status_code == 200
        assert r.json() == []

    async def test_shares_isolated(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        owner = 500
        reader_a = 501
        reader_b = 502

        f = await upload_file(client, ns, "/ex/mine.txt", ["ex1"], user_id=owner)
        await client.post(
            "/sharing/add",
            json={
                "file_id": f["file_id"],
                "user_id": reader_a,
                "access_type": "reader",
            },
            headers={"X-User-Id": str(owner)},
        )

        ra = await client.get("/sharing/list", params={"user_id": reader_a})
        assert len(ra.json()) == 1

        rb = await client.get("/sharing/list", params={"user_id": reader_b})
        assert len(rb.json()) == 0


@pytest.mark.asyncio
class TestFR8Metadata:
    """FR-8: file metadata."""

    async def test_metadata_correct(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        seeds = ["m1", "m2", "m3"]
        body = await upload_file(client, ns, "/meta/info.dat", seeds)

        r = await client.get(f"/files/{body['file_id']}/metadata")
        assert r.status_code == 200
        meta = r.json()
        assert meta["block_count"] == 3
        assert meta["size"] == 3 * BLOCK_SIZE
        assert meta["revision"] == 1
        assert meta["is_deleted"] is False

    async def test_metadata_after_delete(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body = await upload_file(client, ns, "/meta/del.dat", ["md1"])

        await client.post(
            "/files/delete",
            json={
                "namespace_id": ns,
                "file_id": body["file_id"],
                "parent_revision": body["revision"],
            },
            headers={"X-User-Id": "1"},
        )

        r = await client.get(f"/files/{body['file_id']}/metadata")
        assert r.status_code == 200
        assert r.json()["is_deleted"] is True

    async def test_metadata_not_found_404(self, client):
        r = await client.get("/files/00000000-0000-0000-0000-000000000000/metadata")
        assert r.status_code == 404

    async def test_metadata_single_block(self, client, fresh_namespace_id):
        ns = fresh_namespace_id
        body = await upload_file(client, ns, "/solo/s.bin", ["solo"])

        r = await client.get(f"/files/{body['file_id']}/metadata")
        assert r.status_code == 200
        meta = r.json()
        assert meta["block_count"] == 1
        assert meta["size"] == BLOCK_SIZE
