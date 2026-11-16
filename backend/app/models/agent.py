import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import EMBEDDING_DIMENSIONS
from app.database import Base

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.subscription import Subscription


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "ix_agents_interest_embedding_hnsw",
            "interest_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"interest_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    industry_category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    interests: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verified_context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    interest_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    comments: Mapped[list["Comment"]] = relationship(back_populates="author")
