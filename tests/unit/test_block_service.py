"""Unit tests for block_service."""

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dropbox.models.block import Block
from dropbox.services.block_service import get_block, store_block

BLOCK_SIZE = 4 * 1024 * 1024


def make_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def make_block_data(size=BLOCK_SIZE):
    """Generate deterministic block data and its hash/b64."""
    raw = b"X" * size
    h = hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(raw).decode("ascii")
    return raw, h, b64


class TestStoreBlock:
    """FR-1: block storage with dedup and idempotency."""

    @pytest.mark.asyncio
    async def test_store_new_block(self):
        session = make_mock_session()
        raw, h, b64 = make_block_data()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        with patch("dropbox.services.block_service._block_path") as mock_path:
            mock_path_obj = MagicMock()
            mock_path.return_value = mock_path_obj

            result = await store_block(session, block_hash=h, data_b64=b64)

        assert result["status"] == "stored"
        assert result["block_hash"] == h
        mock_path_obj.parent.mkdir.assert_called_once()
        mock_path_obj.write_bytes.assert_called_once_with(raw)

    @pytest.mark.asyncio
    async def test_store_duplicate_block(self):
        session = make_mock_session()
        raw, h, b64 = make_block_data()
        existing = Block(block_hash=h, size=len(raw), ref_count=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute.return_value = mock_result

        with patch("dropbox.services.block_service._block_path") as mock_path:
            mock_path_obj = MagicMock()
            mock_path_obj.exists.return_value = True
            mock_path.return_value = mock_path_obj

            result = await store_block(session, block_hash=h, data_b64=b64)

        assert result["status"] == "already_exists"
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_block_hash_mismatch(self):
        from fastapi import HTTPException

        session = make_mock_session()
        _, h, b64 = make_block_data()
        # Use wrong hash
        wrong_h = "a" * 64

        with pytest.raises(HTTPException) as exc:
            await store_block(session, block_hash=wrong_h, data_b64=b64)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_store_block_invalid_base64(self):
        from fastapi import HTTPException

        session = make_mock_session()

        with pytest.raises(HTTPException) as exc:
            await store_block(session, block_hash="a" * 64, data_b64="!!!not-base64!!!")
        assert exc.value.status_code == 422


class TestGetBlock:
    """FR-2: block retrieval."""

    @pytest.mark.asyncio
    async def test_get_existing_block(self):
        session = make_mock_session()
        raw, h, b64 = make_block_data(100)  # small for test
        existing = Block(block_hash=h, size=len(raw), ref_count=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute.return_value = mock_result

        with patch("dropbox.services.block_service._block_path") as mock_path:
            mock_path_obj = MagicMock()
            mock_path_obj.exists.return_value = True
            mock_path_obj.read_bytes.return_value = raw
            mock_path.return_value = mock_path_obj

            result = await get_block(session, h)

        assert result is not None
        assert result["block_hash"] == h
        assert result["data"] == b64

    @pytest.mark.asyncio
    async def test_get_nonexistent_block(self):
        session = make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await get_block(session, "a" * 64)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_block_file_missing(self):
        session = make_mock_session()
        existing = Block(block_hash="b" * 64, size=100, ref_count=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute.return_value = mock_result

        with patch("dropbox.services.block_service._block_path") as mock_path:
            mock_path_obj = MagicMock()
            mock_path_obj.exists.return_value = False
            mock_path.return_value = mock_path_obj

            result = await get_block(session, "b" * 64)

        assert result is None
