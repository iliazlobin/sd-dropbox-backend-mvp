from uuid import UUID

from pydantic import BaseModel


class AddShareRequest(BaseModel):
    file_id: UUID
    user_id: int
    access_type: str = "reader"


class ShareResponse(BaseModel):
    share_id: UUID
    file_id: UUID
    owner_id: int
    shared_with: int
    access_type: str


class ShareListItem(BaseModel):
    file_id: UUID
    path: str
    owner_id: int
    access_type: str
