"""Blocks router: /blocks/* endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.database import get_session
from dropbox.schemas.block import BlockPutRequest, BlockPutResponse, BlockResponse
from dropbox.services import block_service

router = APIRouter(prefix="/blocks", tags=["blocks"])


@router.post("/put", response_model=BlockPutResponse, status_code=201)
async def put_block(
    body: BlockPutRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await block_service.store_block(
            session,
            block_hash=body.block_hash,
            data_b64=body.data,
        )
        await session.commit()
        return result
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{block_hash}", response_model=BlockResponse)
async def get_block(
    block_hash: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await block_service.get_block(session, block_hash)
    if result is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return result
