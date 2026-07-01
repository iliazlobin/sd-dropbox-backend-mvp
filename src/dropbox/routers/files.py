"""Files router: /files/* endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dropbox.database import get_session
from dropbox.schemas.file import (
    CommitRequest,
    CommitResponse,
    DeleteRequest,
    DeleteResponse,
    FileListItem,
    FileMetadataResponse,
    FileResponse,
)
from dropbox.services import file_service
from dropbox.services.file_service import ConflictError

router = APIRouter(prefix="/files", tags=["files"])


async def get_caller_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> int | None:
    """Extract caller user_id from X-User-Id header. Returns None if absent."""
    if x_user_id is None:
        return None
    try:
        return int(x_user_id)
    except ValueError:
        return None


# Static routes MUST come before parameterized routes

@router.post("/commit", response_model=CommitResponse, status_code=201)
async def commit_file(
    body: CommitRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await file_service.commit_file(
            session,
            namespace_id=body.namespace_id,
            path=body.path,
            blocklist=body.blocklist,
            parent_revision=body.parent_revision,
        )
        await session.commit()
        return result
    except ConflictError as e:
        await session.rollback()
        return JSONResponse(
            status_code=409,
            content={"error": "Conflict", "current_revision": e.current_revision},
        )
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/delete", response_model=DeleteResponse)
async def delete_file(
    body: DeleteRequest,
    caller_id: Annotated[int | None, Depends(get_caller_user_id)] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await file_service.delete_file(
            session,
            namespace_id=body.namespace_id,
            file_id=body.file_id,
            parent_revision=body.parent_revision,
            caller_id=caller_id,
        )
        await session.commit()
        return result
    except ConflictError as e:
        await session.rollback()
        return JSONResponse(
            status_code=409,
            content={"error": "Conflict", "current_revision": e.current_revision},
        )
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/list", response_model=list[FileListItem])
async def list_files(
    namespace_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await file_service.list_files(session, namespace_id)


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await file_service.get_file(session, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "file_id": str(row.file_id),
        "path": row.path,
        "blocklist": row.blocklist,
        "revision": row.revision,
        "size": row.size,
        "modified_at": row.modified_at.isoformat() if row.modified_at else "",
    }


@router.get("/{file_id}/metadata", response_model=FileMetadataResponse)
async def get_file_metadata(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    meta = await file_service.get_file_metadata(session, file_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="File not found")
    return meta
