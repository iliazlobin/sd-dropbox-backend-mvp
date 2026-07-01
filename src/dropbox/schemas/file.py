from uuid import UUID

from pydantic import BaseModel


class CommitRequest(BaseModel):
    namespace_id: int
    path: str
    blocklist: list[str]
    parent_revision: int | None = None


class CommitResponse(BaseModel):
    file_id: UUID
    revision: int
    need_blocks: list[str]


class FileResponse(BaseModel):
    file_id: UUID
    path: str
    blocklist: list[str]
    revision: int
    size: int
    modified_at: str


class FileMetadataResponse(BaseModel):
    file_id: UUID
    path: str
    block_count: int
    size: int
    revision: int
    modified_at: str
    is_deleted: bool


class FileListItem(BaseModel):
    file_id: UUID
    path: str
    revision: int
    size: int
    modified_at: str


class DeleteRequest(BaseModel):
    namespace_id: int
    file_id: UUID
    parent_revision: int


class DeleteResponse(BaseModel):
    file_id: UUID
    deleted: bool


class ConflictError(BaseModel):
    error: str = "Conflict"
    current_revision: int
