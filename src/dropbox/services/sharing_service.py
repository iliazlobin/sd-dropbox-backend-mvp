"""Sharing service: share, list-shares, access-check logic."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.models.file import File
from dropbox.models.share import Share


async def add_share(
    session: AsyncSession,
    *,
    file_id: uuid.UUID,
    owner_id: int,
    user_id: int,
    access_type: str,
) -> dict:
    """Grant read access to a user. Returns share info."""
    # Check file exists
    stmt = select(File).where(File.file_id == file_id)
    result = await session.execute(stmt)
    file_row = result.scalar_one_or_none()
    if file_row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")

    # Prevent self-share
    if owner_id == user_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Cannot share with self")

    # Check for existing share
    existing_stmt = select(Share).where(
        Share.file_id == file_id,
        Share.shared_with == user_id,
    )
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none() is not None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Share already exists")

    share = Share(
        share_id=uuid.uuid4(),
        file_id=file_id,
        owner_id=owner_id,
        shared_with=user_id,
        access_type=access_type,
    )
    session.add(share)
    await session.flush()

    return {
        "share_id": str(share.share_id),
        "file_id": str(share.file_id),
        "owner_id": share.owner_id,
        "shared_with": share.shared_with,
        "access_type": share.access_type,
    }


async def list_shares(session: AsyncSession, user_id: int) -> list[dict]:
    """List files shared with a user."""
    stmt = (
        select(Share, File.path)
        .join(File, Share.file_id == File.file_id)
        .where(Share.shared_with == user_id)
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [
        {
            "file_id": str(r.Share.file_id),
            "path": r.path,
            "owner_id": r.Share.owner_id,
            "access_type": r.Share.access_type,
        }
        for r in rows
    ]


async def check_access(session: AsyncSession, file_id: uuid.UUID, user_id: int) -> bool:
    """Check if a user has any access to a file (owner or shared)."""
    stmt = select(Share).where(
        Share.file_id == file_id,
        Share.shared_with == user_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None
