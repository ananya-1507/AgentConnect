import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, OutboxEvent, Post, RoutingLog
from app.schemas import PostCreate, PostPublic, RoutingLogPublic
from app.security import get_current_agent

router = APIRouter(prefix="/api/v1/posts", tags=["posts"])


def _post_public(post: Post) -> PostPublic:
    data = {
        "id": post.id,
        "author_agent_id": post.author_agent_id,
        "title": post.title,
        "content": post.content,
        "category": post.category,
        "topics": [item.strip() for item in post.topics.split(",") if item.strip()],
        "routing_status": post.routing_status,
        "expires_at": post.expires_at,
        "created_at": post.created_at,
    }
    return PostPublic(**data)


@router.post("", response_model=PostPublic, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if idempotency_key:
        existing = (
            db.query(Post)
            .filter(
                Post.author_agent_id == current_agent.id,
                Post.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing:
            response.status_code = status.HTTP_200_OK
            return _post_public(existing)

    post = Post(
        author_agent_id=current_agent.id,
        title=payload.title,
        content=payload.content,
        category=payload.category,
        topics=", ".join(dict.fromkeys(topic.strip() for topic in payload.topics if topic.strip())),
        expires_at=payload.expires_at,
        idempotency_key=idempotency_key,
    )
    if post.expires_at and post.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    db.add(post)
    db.flush()
    db.add(OutboxEvent(post_id=post.id))
    db.commit()
    db.refresh(post)
    return _post_public(post)


@router.get("", response_model=list[PostPublic])
def list_posts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    query = db.query(Post).filter(or_(Post.expires_at.is_(None), Post.expires_at > now))
    if category:
        query = query.filter(Post.category == category)
    posts = query.order_by(Post.created_at.desc()).offset(offset).limit(limit).all()
    return [_post_public(post) for post in posts]


@router.get("/{post_id}", response_model=PostPublic)
def get_post(post_id: uuid.UUID, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return _post_public(post)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.author_agent_id != current_agent.id:
        raise HTTPException(status_code=403, detail="Only the author can delete this post")
    db.delete(post)
    db.commit()


@router.get("/{post_id}/routing-logs", response_model=list[RoutingLogPublic])
def post_routing_logs(
    post_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    query = db.query(RoutingLog).filter(RoutingLog.post_id == post_id)
    if post.author_agent_id != current_agent.id:
        query = query.filter(RoutingLog.target_agent_id == current_agent.id)
    return query.order_by(RoutingLog.created_at.desc()).all()
