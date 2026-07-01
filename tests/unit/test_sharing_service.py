"""Unit tests for sharing_service."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from dropbox.models.file import File
from dropbox.models.share import Share
from dropbox.services.sharing_service import add_share, check_access, list_shares


def make_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class TestAddShare:
    """FR-6: file sharing."""

    @pytest.mark.asyncio
    async def test_add_share_creates_share(self):
        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/f.txt",
            blocklist=[],
            revision=1,
            is_deleted=False,
            size=0,
        )
        # First execute: find file
        mock_file_result = MagicMock()
        mock_file_result.scalar_one_or_none.return_value = f
        # Second execute: check existing share
        mock_share_result = MagicMock()
        mock_share_result.scalar_one_or_none.return_value = None
        session.execute.side_effect = [mock_file_result, mock_share_result]

        result = await add_share(
            session,
            file_id=f.file_id,
            owner_id=100,
            user_id=200,
            access_type="reader",
        )

        assert result["owner_id"] == 100
        assert result["shared_with"] == 200
        assert result["access_type"] == "reader"
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_share_self_share_raises_409(self):
        from fastapi import HTTPException

        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/f.txt",
            blocklist=[],
            revision=1,
            is_deleted=False,
            size=0,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        session.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            await add_share(
                session,
                file_id=f.file_id,
                owner_id=100,
                user_id=100,  # self
                access_type="reader",
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_add_share_nonexistent_file_raises_404(self):
        from fastapi import HTTPException

        session = make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            await add_share(
                session,
                file_id=uuid.uuid4(),
                owner_id=100,
                user_id=200,
                access_type="reader",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_share_duplicate_raises_409(self):
        from fastapi import HTTPException

        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/f.txt",
            blocklist=[],
            revision=1,
            is_deleted=False,
            size=0,
        )
        existing_share = Share(
            share_id=uuid.uuid4(),
            file_id=f.file_id,
            owner_id=100,
            shared_with=200,
            access_type="reader",
        )
        mock_file_result = MagicMock()
        mock_file_result.scalar_one_or_none.return_value = f
        mock_share_result = MagicMock()
        mock_share_result.scalar_one_or_none.return_value = existing_share
        session.execute.side_effect = [mock_file_result, mock_share_result]

        with pytest.raises(HTTPException) as exc:
            await add_share(
                session,
                file_id=f.file_id,
                owner_id=100,
                user_id=200,
                access_type="reader",
            )
        assert exc.value.status_code == 409


class TestListShares:
    """FR-7: list shared files."""

    @pytest.mark.asyncio
    async def test_list_shares_returns_entries(self):
        session = make_mock_session()
        f1 = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/a.txt",
            blocklist=[],
            revision=1,
            is_deleted=False,
            size=0,
        )
        s1 = Share(
            share_id=uuid.uuid4(),
            file_id=f1.file_id,
            owner_id=100,
            shared_with=200,
            access_type="reader",
        )
        mock_row = MagicMock()
        mock_row.Share = s1
        mock_row.path = "/a.txt"
        mock_result = MagicMock()
        mock_result.all.return_value = [mock_row]
        session.execute.return_value = mock_result

        shares = await list_shares(session, user_id=200)
        assert len(shares) == 1
        assert shares[0]["path"] == "/a.txt"


class TestCheckAccess:
    """Access control for shared files."""

    @pytest.mark.asyncio
    async def test_check_access_shared_user(self):
        session = make_mock_session()
        share = Share(
            share_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            owner_id=100,
            shared_with=200,
            access_type="reader",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = share
        session.execute.return_value = mock_result

        result = await check_access(session, uuid.uuid4(), user_id=200)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_access_no_share(self):
        session = make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await check_access(session, uuid.uuid4(), user_id=999)
        assert result is False
