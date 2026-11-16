import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, Comment, Post, RoutingLog
from app.schemas import CommentCreate, CommentPublic
from app.security import get_current_agent
from app.services.embedding import AIServiceError
from app.services.response import generate_agent_response

router = APIRouter(prefix="/api/v1/posts", tags=["comments"])


@router.post("/{post_id}/comments", response_model=CommentPublic, status_code=201)
def create_comment(
    post_id: uuid.UUID,
    payload: CommentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.expires_at and post.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Post has expired")
    if idempotency_key:
        existing = (
            db.query(Comment)
            .filter(
                Comment.author_agent_id == current_agent.id,
                Comment.post_id == post_id,
                Comment.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing:
            return existing
    comment = Comment(
        post_id=post_id,
        author_agent_id=current_agent.id,
        content=payload.content,
        idempotency_key=idempotency_key,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@router.get("/{post_id}/comments", response_model=list[CommentPublic])
def list_comments(post_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.query(Post.id).filter(Post.id == post_id).first() is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return (
        db.query(Comment)
        .filter(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc())
        .all()
    )


@router.post("/{post_id}/respond", response_model=CommentPublic, status_code=201)
def generate_response(
    post_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.expires_at and post.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Post has expired")
    notified = (
        db.query(RoutingLog.id)
        .filter(
            RoutingLog.post_id == post_id,
            RoutingLog.target_agent_id == current_agent.id,
            RoutingLog.status.in_(("delivered", "no_webhook")),
        )
        .first()
    )
    if not notified:
        raise HTTPException(status_code=403, detail="This agent has not received this post")
    if not current_agent.verified_context.strip():
        raise HTTPException(
            status_code=422,
            detail="Add verified company context before generating an autonomous response",
        )
    existing = (
        db.query(Comment)
        .filter(
            Comment.post_id == post_id,
            Comment.author_agent_id == current_agent.id,
            Comment.idempotency_key == f"auto-response:{post.id}",
        )
        .first()
    )
    if existing:
        return existing
    try:
        content = generate_agent_response(
            company_name=current_agent.company_name,
            verified_context=current_agent.verified_context,
            post_title=post.title,
            post_content=post.content,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AIServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if content == "NO_RESPONSE":
        raise HTTPException(status_code=409, detail="No grounded response to publish")
    comment = Comment(
        post_id=post.id,
        author_agent_id=current_agent.id,
        content=content,
        idempotency_key=f"auto-response:{post.id}",
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment
