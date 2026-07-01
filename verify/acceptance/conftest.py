"""Shared fixtures and helpers for the Dropbox MVP black-box acceptance suite.

These tests do NOT import `src.dropbox`. They talk to the running system
via HTTP at API_BASE_URL. Test isolation is achieved through unique
namespace_ids / user_ids per test — no database clearing required.
"""

import base64
import hashlib
import os
import uuid

import httpx
import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url():
    return API_BASE_URL


@pytest.fixture(scope="session")
def client(base_url):
    """Session-scoped httpx client for the entire acceptance run."""
    with httpx.Client(base_url=base_url, timeout=30) as c:
        yield c


@pytest.fixture
def fresh_namespace_id():
    """Unique namespace_id per test to ensure isolation."""
    return uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF  # positive 63-bit int


@pytest.fixture
def fresh_user_id():
    """Unique user_id per test for sharing tests."""
    return uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Block helpers — black-box only, no app imports
# ---------------------------------------------------------------------------

BLOCK_SIZE = 4 * 1024 * 1024  # 4 MB


def make_block_data(seed: str) -> bytes:
    """Generate deterministic 4 MB block content from a seed string.
    Uses a simple expanding pattern so content is unique per seed but
    reproducible for the same seed.
    """
    h = hashlib.sha256(seed.encode()).digest()
    # Repeat the hash to fill 4 MB
    repeats = (BLOCK_SIZE // len(h)) + 1
    return (h * repeats)[:BLOCK_SIZE]


def make_block_hash(seed: str) -> str:
    """SHA-256 hex of the 4 MB block generated from seed."""
    return hashlib.sha256(make_block_data(seed)).hexdigest()


def make_block_b64(seed: str) -> str:
    """Base64-encoded 4 MB block content for the PUT endpoint."""
    return base64.b64encode(make_block_data(seed)).decode("ascii")


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def upload_file(client, namespace_id, path, seeds, user_id=1):
    """Full two-phase upload: commit → upload missing blocks → recommit.

    Args:
        seeds: list of strings, one per block (e.g. ["a","b","c"] for 3 blocks)
    Returns: parsed response body from the final commit (201)
    """
    blocklist = [make_block_hash(s) for s in seeds]

    # Phase 1: commit blocklist
    r = client.post(
        "/files/commit",
        json={
            "namespace_id": namespace_id,
            "path": path,
            "blocklist": blocklist,
            "parent_revision": None,
        },
        headers={"X-User-Id": str(user_id)},
    )
    assert r.status_code == 201, f"Phase 1 commit failed: {r.status_code} {r.text}"
    body = r.json()
    need_blocks = body.get("need_blocks", [])

    # Phase 2: upload missing blocks
    for seed in seeds:
        h = make_block_hash(seed)
        if h in need_blocks:
            r2 = client.post(
                "/blocks/put",
                json={
                    "block_hash": h,
                    "data": make_block_b64(seed),
                },
            )
            assert r2.status_code == 201, f"Block PUT failed for {h[:12]}: {r2.status_code} {r2.text}"

    # Phase 3: recommit (all blocks now exist)
    if need_blocks:
        r3 = client.post(
            "/files/commit",
            json={
                "namespace_id": namespace_id,
                "path": path,
                "blocklist": blocklist,
                "parent_revision": None,
            },
            headers={"X-User-Id": str(user_id)},
        )
        assert r3.status_code == 201, f"Recommit failed: {r3.status_code} {r3.text}"
        return r3.json()

    return body


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def assert_200(r):
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    return r.json()


def assert_201(r):
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    return r.json()


def assert_404(r):
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"
    return r.json()


def assert_409(r):
    assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"
    return r.json()


def assert_422(r):
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
    return r.json()
