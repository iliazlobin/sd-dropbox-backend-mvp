from pydantic import BaseModel


class BlockPutRequest(BaseModel):
    block_hash: str
    data: str


class BlockResponse(BaseModel):
    block_hash: str
    data: str


class BlockPutResponse(BaseModel):
    block_hash: str
    status: str
