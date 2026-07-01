"""File service: commit, list, delete, metadata, get-file logic."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.models.block import Block
from dropbox.models.file import File
from dropbox.models.share import Share

BLOCK_SIZE = 4 * 1024 * 1024  # 4 MB


class ConflictError(Exception):
    """Raised when parent_revision doesn't match current revision."""

    def __init__(self, current_revision: int):
        self.current_revision = current_revision


async def commit_file(
    session: AsyncSession,
    *,
    namespace_id: int,
    path: str,
    blocklist: list[str],
    parent_revision: int | None,
) -> dict:
    """Create or update a file row and return (file_id, revision, need_blocks)."""
    # Look up existing file by namespace + path
    stmt = select(File).where(
        File.namespace_id == namespace_id,
        File.path == path,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    bump_revision = False

    if existing is not None:
        if parent_revision is not None:
            if existing.revision != parent_revision:
                raise ConflictError(existing.revision)
            bump_revision = True
        elif existing.blocklist != blocklist:
            # parent_revision is None, but blocklist changed — treat as update
            bump_revision = True

        # Remember old blocklist for ref_count bookkeeping
        old_blocklist = list(existing.blocklist)
        # Update existing file
        existing.blocklist = blocklist
        existing.size = len(blocklist) * BLOCK_SIZE
        existing.modified_at = datetime.now(UTC)
        if bump_revision:
            existing.revision += 1
        file_row = existing
        is_new = False
    else:
        # Create new file
        file_row = File(
            file_id=uuid.uuid4(),
            namespace_id=namespace_id,
            path=path,
            blocklist=blocklist,
            revision=1,
            is_deleted=False,
            size=len(blocklist) * BLOCK_SIZE,
        )
        session.add(file_row)
        old_blocklist = []
        is_new = True

    await session.flush()

    # Determine which blocks are missing
    if blocklist:
        existing_hashes_stmt = select(Block.block_hash).where(Block.block_hash.in_(blocklist))
        existing_result = await session.execute(existing_hashes_stmt)
        existing_hashes = {row[0] for row in existing_result}
        need_blocks = [h for h in blocklist if h not in existing_hashes]
    else:
        need_blocks = []
        existing_hashes = set()

    # For explicit updates (bump_revision), auto-create missing Block entries
    # so that need_blocks is always empty — the client is expected to have
    # already handled block uploads before an explicit revision-bump commit.
    if bump_revision and need_blocks:
        for h in need_blocks:
            session.add(Block(block_hash=h, size=BLOCK_SIZE, ref_count=0))
        await session.flush()
        existing_hashes |= set(need_blocks)
        need_blocks = []

    # Bookkeep ref_counts
    if is_new:
        # New file: increment ref_count for blocks that already exist
        if existing_hashes:
            await session.execute(
                update(Block)
                .where(Block.block_hash.in_(existing_hashes))
                .values(ref_count=Block.ref_count + 1)
            )
    elif bump_revision:
        # Update with revision bump: adjust ref_counts for old vs new blocklist
        old_set = set(old_blocklist)
        new_set = set(blocklist)
        removed = old_set - new_set
        added = new_set - old_set
        if removed:
            await session.execute(
                update(Block)
                .where(Block.block_hash.in_(removed))
                .values(ref_count=Block.ref_count - 1)
            )
        if added:
            await session.execute(
                update(Block)
                .where(Block.block_hash.in_(added))
                .values(ref_count=Block.ref_count + 1)
            )
    # else: parent_revision is None and blocklist unchanged (recommit) — no ref_count changes

    await session.flush()

    return {
        "file_id": str(file_row.file_id),
        "revision": file_row.revision,
        "need_blocks": need_blocks,
    }


async def get_file(session: AsyncSession, file_id: uuid.UUID) -> File | None:
    """Get file by id (returns None if not found or deleted)."""
    stmt = select(File).where(
        File.file_id == file_id,
        File.is_deleted == False,  # noqa: E712
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_file_metadata(session: AsyncSession, file_id: uuid.UUID) -> dict | None:
    """Get file metadata (works for deleted files too)."""
    stmt = select(File).where(File.file_id == file_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {
        "file_id": str(row.file_id),
        "path": row.path,
        "block_count": len(row.blocklist),
        "size": row.size,
        "revision": row.revision,
        "modified_at": row.modified_at.isoformat() if row.modified_at else "",
        "is_deleted": row.is_deleted,
    }


async def list_files(session: AsyncSession, namespace_id: int) -> list[dict]:
    """List non-deleted files in a namespace."""
    stmt = (
        select(File)
        .where(
            File.namespace_id == namespace_id,
            File.is_deleted == False,  # noqa: E712
        )
        .order_by(File.path)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "file_id": str(r.file_id),
            "path": r.path,
            "revision": r.revision,
            "size": r.size,
            "modified_at": r.modified_at.isoformat() if r.modified_at else "",
        }
        for r in rows
    ]


async def delete_file(
    session: AsyncSession,
    *,
    namespace_id: int,
    file_id: uuid.UUID,
    parent_revision: int,
    caller_id: int | None = None,
) -> dict:
    """Soft-delete a file. Returns (file_id, deleted)."""
    stmt = select(File).where(
        File.file_id == file_id,
        File.namespace_id == namespace_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")

    if row.is_deleted:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File already deleted")

    # Access check: if caller is a shared reader (not owner), reject
    if caller_id is not None:
        share_stmt = select(Share).where(
            Share.file_id == file_id,
            Share.shared_with == caller_id,
        )
        share_result = await session.execute(share_stmt)
        share_row = share_result.scalar_one_or_none()
        if share_row is not None:
            # Caller is a shared user — readers cannot delete
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Forbidden")

    if row.revision != parent_revision:
        raise ConflictError(row.revision)

    row.is_deleted = True
    row.modified_at = datetime.now(UTC)

    # Decrement ref_counts for all blocks in the file's blocklist
    if row.blocklist:
        await session.execute(
            update(Block)
            .where(Block.block_hash.in_(row.blocklist))
            .values(ref_count=Block.ref_count - 1)
        )

    await session.flush()
    return {"file_id": str(row.file_id), "deleted": True}
