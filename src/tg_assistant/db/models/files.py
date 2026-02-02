from datetime import datetime

from sqlalchemy import DateTime, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tg_assistant.db.base import Base


class StoredFile(Base):
    __tablename__ = "files"

    __table_args__ = (
        UniqueConstraint("user_id", "sha256", name="uq_files_user_sha256"),
        UniqueConstraint("user_id", "tg_file_unique_id", name="uq_files_user_tg_file_unique_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    orig_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    tg_file_unique_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    local_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
