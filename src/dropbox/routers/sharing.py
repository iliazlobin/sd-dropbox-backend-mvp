"""Sharing router: /sharing/* endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.database import get_session
from dropbox.schemas.sharing import AddShareRequest, ShareListItem, ShareResponse
from dropbox.services import sharing_service

router = APIRouter(prefix="/sharing", tags=["sharing"])


async def get_caller_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> int:
    """Extract caller user_id from X-User-Id header."""
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header required")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-User-Id header") from None


@router.post("/add", response_model=ShareResponse, status_code=201)
async def add_share(
    body: AddShareRequest,
    caller_id: Annotated[int, Depends(get_caller_user_id)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await sharing_service.add_share(
            session,
            file_id=body.file_id,
            owner_id=caller_id,
            user_id=body.user_id,
            access_type=body.access_type,
        )
        await session.commit()
        return result
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/list", response_model=list[ShareListItem])
async def list_shares(
    user_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await sharing_service.list_shares(session, user_id)
