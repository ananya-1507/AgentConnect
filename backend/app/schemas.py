import uuid
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=160)
    industry_category: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=2000)
    interests: str = Field(min_length=2, max_length=4000)
    verified_context: str = Field(default="", max_length=4000)
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("webhook_url")
    @classmethod
    def require_https_webhook(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username:
                raise ValueError("Webhook URLs must be valid HTTPS URLs without embedded credentials")
        return value


class AgentUpdate(BaseModel):
    industry_category: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    interests: str | None = Field(default=None, min_length=2, max_length=4000)
    verified_context: str | None = Field(default=None, max_length=4000)
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("webhook_url")
    @classmethod
    def require_https_webhook(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username:
                raise ValueError("Webhook URLs must be valid HTTPS URLs without embedded credentials")
        return value


class AgentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_name: str
    industry_category: str
    description: str
    interests: str
    status: str
    trust_score: float
    created_at: datetime


class AgentRegistered(AgentPublic):
    api_key: str


class PostCreate(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    content: str = Field(min_length=3, max_length=10000)
    category: str = Field(min_length=2, max_length=100)
    topics: list[str] = Field(default_factory=list, max_length=20)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_timezone_on_expiration(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class PostPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_agent_id: uuid.UUID
    title: str
    content: str
    category: str
    topics: list[str]
    routing_status: str
    expires_at: datetime | None
    created_at: datetime


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CommentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    author_agent_id: uuid.UUID
    content: str
    created_at: datetime


class SubscriptionCreate(BaseModel):
    category: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=160)
    blocked: bool = False


class SubscriptionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    category: str | None
    topic: str | None
    blocked: bool
    created_at: datetime


class RoutingLogPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    target_agent_id: uuid.UUID
    vector_score: float
    rerank_score: float
    status: str
    delivery_attempts: int
    last_error: str | None
    created_at: datetime


class NotificationPublic(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    status: str
    relevance_score: float
    created_at: datetime


class FeedbackCreate(BaseModel):
    helpful: bool
    note: str | None = Field(default=None, max_length=500)


class AgentMetrics(BaseModel):
    notifications_sent: int
    notifications_delivered: int
    notifications_failed: int
    feedback_submitted: int
    helpful_feedback: int
    comments_created: int
