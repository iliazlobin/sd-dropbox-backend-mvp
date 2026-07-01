"""Unit tests for file_service — isolated with mock DB."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from dropbox.models.file import File
from dropbox.services.file_service import (
    ConflictError,
    commit_file,
    delete_file,
    get_file,
    get_file_metadata,
    list_files,
)


def make_mock_session():
    """Create a mock AsyncSession."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


class TestCommitFile:
    """FR-1, FR-5: file commit with dedup and conflict detection."""

    @pytest.mark.asyncio
    async def test_commit_new_file_creates_row(self):
        """New file with parent_revision=None creates a file at revision 1."""
        session = make_mock_session()
        # No existing file
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await commit_file(
            session,
            namespace_id=1,
            path="/test.txt",
            blocklist=["abc123"],
            parent_revision=None,
        )

        session.add.assert_called_once()
        added_file = session.add.call_args[0][0]
        assert added_file.revision == 1
        assert added_file.path == "/test.txt"
        assert added_file.blocklist == ["abc123"]
        assert result["revision"] == 1
        assert isinstance(result["file_id"], str)

    @pytest.mark.asyncio
    async def test_commit_updates_existing_with_bump(self):
        """Explicit parent_revision bumps revision."""
        session = make_mock_session()
        existing = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/test.txt",
            blocklist=["old_hash"],
            revision=1,
            is_deleted=False,
            size=4 * 1024 * 1024,
        )

        # Mock: first execute returns existing file, subsequent calls return empty
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = existing
        mock_result2 = MagicMock()
        mock_result2.scalar_one_or_none.return_value = None
        mock_result2.__iter__ = lambda self: iter([("old_hash",)])
        mock_default = MagicMock()
        mock_default.scalar_one_or_none.return_value = None
        mock_default.__iter__ = lambda self: iter([])
        _side_effect = [mock_result1, mock_result2, mock_default]
        session.execute.side_effect = (
            lambda *a, **kw: _side_effect.pop(0) if _side_effect else mock_default
        )

        result = await commit_file(
            session,
            namespace_id=1,
            path="/test.txt",
            blocklist=["new_hash"],  # changed blocklist
            parent_revision=1,
        )

        assert result["revision"] == 2

    @pytest.mark.asyncio
    async def test_commit_returns_conflict_on_stale_revision(self):
        """Stale parent_revision raises ConflictError."""
        session = make_mock_session()
        existing = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/test.txt",
            blocklist=["h1"],
            revision=2,  # current is 2
            is_deleted=False,
            size=4 * 1024 * 1024,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute.return_value = mock_result

        with pytest.raises(ConflictError) as exc:
            await commit_file(
                session,
                namespace_id=1,
                path="/test.txt",
                blocklist=["h2"],
                parent_revision=1,  # stale!
            )
        assert exc.value.current_revision == 2

    @pytest.mark.asyncio
    async def test_commit_recommit_same_blocklist_no_bump(self):
        """Recommit with same blocklist does not bump revision."""
        session = make_mock_session()
        existing = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/test.txt",
            blocklist=["abc"],
            revision=1,
            is_deleted=False,
            size=4 * 1024 * 1024,
        )
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = existing
        mock_result2 = MagicMock()
        mock_result2.__iter__ = lambda self: iter([("abc",)])
        session.execute.side_effect = [mock_result1, mock_result2]

        result = await commit_file(
            session,
            namespace_id=1,
            path="/test.txt",
            blocklist=["abc"],  # same blocklist
            parent_revision=None,
        )

        assert result["revision"] == 1  # no bump

    @pytest.mark.asyncio
    async def test_commit_returns_need_blocks(self):
        """Missing blocks appear in need_blocks."""
        session = make_mock_session()
        # No existing file
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = None
        # No existing blocks
        mock_result2 = MagicMock()
        mock_result2.__iter__ = lambda self: iter([])
        session.execute.side_effect = [mock_result1, mock_result2]

        result = await commit_file(
            session,
            namespace_id=1,
            path="/new.txt",
            blocklist=["hash1", "hash2"],
            parent_revision=None,
        )

        assert sorted(result["need_blocks"]) == ["hash1", "hash2"]


class TestGetFile:
    """FR-2: file retrieval."""

    @pytest.mark.asyncio
    async def test_get_file_returns_file(self):
        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/doc.txt",
            blocklist=["h1"],
            revision=1,
            is_deleted=False,
            size=4 * 1024 * 1024,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        session.execute.return_value = mock_result

        result = await get_file(session, f.file_id)
        assert result is not None
        assert result.path == "/doc.txt"

    @pytest.mark.asyncio
    async def test_get_file_deleted_returns_none(self):
        session = make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await get_file(session, uuid.uuid4())
        assert result is None


class TestGetFileMetadata:
    """FR-8: file metadata."""

    @pytest.mark.asyncio
    async def test_metadata_returns_correct_fields(self):
        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/meta.txt",
            blocklist=["h1", "h2", "h3"],
            revision=2,
            is_deleted=False,
            size=12 * 1024 * 1024,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        session.execute.return_value = mock_result

        meta = await get_file_metadata(session, f.file_id)
        assert meta["block_count"] == 3
        assert meta["size"] == 12 * 1024 * 1024
        assert meta["revision"] == 2
        assert meta["is_deleted"] is False

    @pytest.mark.asyncio
    async def test_metadata_not_found_returns_none(self):
        session = make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await get_file_metadata(session, uuid.uuid4())
        assert result is None


class TestListFiles:
    """FR-3: namespace-scoped file listing."""

    @pytest.mark.asyncio
    async def test_list_files_returns_non_deleted(self):
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
        f2 = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/b.txt",
            blocklist=[],
            revision=1,
            is_deleted=False,
            size=0,
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [f1, f2]
        session.execute.return_value = mock_result

        files = await list_files(session, namespace_id=1)
        assert len(files) == 2
        assert {f["path"] for f in files} == {"/a.txt", "/b.txt"}


class TestDeleteFile:
    """FR-4: soft-delete."""

    @pytest.mark.asyncio
    async def test_delete_marks_file_deleted(self):
        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/del.txt",
            blocklist=["h1"],
            revision=1,
            is_deleted=False,
            size=4 * 1024 * 1024,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        # Second execute for share check
        mock_share_result = MagicMock()
        mock_share_result.scalar_one_or_none.return_value = None
        # Third execute for ref_count update
        mock_update = MagicMock()
        mock_update.scalar_one_or_none.return_value = None
        mock_update.__iter__ = lambda self: iter([])
        _side_effect = [mock_result, mock_share_result, mock_update]
        session.execute.side_effect = (
            lambda *a, **kw: _side_effect.pop(0) if _side_effect else mock_update
        )

        result = await delete_file(
            session,
            namespace_id=1,
            file_id=f.file_id,
            parent_revision=1,
            caller_id=100,
        )
        assert f.is_deleted is True
        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_already_deleted_raises_404(self):
        from fastapi import HTTPException

        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/del.txt",
            blocklist=[],
            revision=1,
            is_deleted=True,
            size=0,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        session.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            await delete_file(
                session,
                namespace_id=1,
                file_id=f.file_id,
                parent_revision=1,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_conflict_raises_409(self):
        session = make_mock_session()
        f = File(
            file_id=uuid.uuid4(),
            namespace_id=1,
            path="/del.txt",
            blocklist=[],
            revision=2,
            is_deleted=False,
            size=0,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = f
        session.execute.return_value = mock_result

        with pytest.raises(ConflictError) as exc:
            await delete_file(
                session,
                namespace_id=1,
                file_id=f.file_id,
                parent_revision=1,  # stale
            )
        assert exc.value.current_revision == 2
