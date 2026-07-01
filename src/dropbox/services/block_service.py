"""Block service: store, retrieve, dedup logic."""

import base64
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.config import settings
from dropbox.models.block import Block

BLOCK_SIZE = 4 * 1024 * 1024  # 4 MB


def _block_path(block_hash: str) -> Path:
    """Filesystem path for a block, content-addressed by SHA-256 hex."""
    return settings.block_storage_path / block_hash[:2] / block_hash[2:4] / block_hash


async def store_block(
    session: AsyncSession, *, block_hash: str, data_b64: str
) -> dict:
    """Store a block. Returns (block_hash, status). Idempotent by hash."""
    # Decode and validate
    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Invalid base64 data") from None

    # Validate hash
    computed = hashlib.sha256(raw).hexdigest()
    if computed != block_hash:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Block hash mismatch") from None

    # Check if block already exists in DB
    stmt = select(Block).where(Block.block_hash == block_hash)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    path = _block_path(block_hash)

    if existing is not None:
        # Block row exists — if file is missing, write it now
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            # Update size if it was a placeholder (size=0 from auto-create)
            if existing.size == 0:
                existing.size = len(raw)
        return {"block_hash": block_hash, "status": "already_exists"}

    # Write to filesystem
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    # Create DB record
    block = Block(
        block_hash=block_hash,
        size=len(raw),
        ref_count=1,
    )
    session.add(block)
    await session.flush()

    return {"block_hash": block_hash, "status": "stored"}


async def get_block(session: AsyncSession, block_hash: str) -> dict | None:
    """Retrieve a block by hash. Returns dict with block_hash + data or None."""
    stmt = select(Block).where(Block.block_hash == block_hash)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is None:
        return None

    path = _block_path(block_hash)
    if not path.exists():
        return None

    raw = path.read_bytes()
    return {
        "block_hash": block_hash,
        "data": base64.b64encode(raw).decode("ascii"),
    }
