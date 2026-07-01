import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from dropbox.models.base import Base


class Share(Base):
    __tablename__ = "shares"

    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.file_id"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shared_with: Mapped[int] = mapped_column(BigInteger, nullable=False)
    access_type: Mapped[str] = mapped_column(String(20), nullable=False, default="reader")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("file_id", "shared_with", name="uq_share_file_user"),
    )

    def __repr__(self) -> str:
        return f"<Share {self.share_id} file={self.file_id} with={self.shared_with}>"
