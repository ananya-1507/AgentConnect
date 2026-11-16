import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, Comment, Feedback, RoutingLog, Subscription
from app.schemas import (
    AgentCreate,
    AgentMetrics,
    AgentPublic,
    AgentRegistered,
    AgentUpdate,
    FeedbackCreate,
    NotificationPublic,
    SubscriptionCreate,
    SubscriptionPublic,
)
from app.security import create_api_key, get_current_agent
from app.services.embedding import AIServiceError, embed_text

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _interest_text(agent: Agent) -> str:
    return (
        f"Company: {agent.company_name}\nIndustry: {agent.industry_category}\n"
        f"Description: {agent.description}\nInterests: {agent.interests}"
    )


@router.post("/register", response_model=AgentRegistered, status_code=status.HTTP_201_CREATED)
def register_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    api_key, digest = create_api_key()
    agent = Agent(
        company_name=payload.company_name,
        industry_category=payload.industry_category,
        description=payload.description,
        interests=payload.interests,
        verified_context=payload.verified_context,
        webhook_url=payload.webhook_url,
        api_key_hash=digest,
    )
    try:
        agent.interest_embedding = embed_text(_interest_text(agent), "RETRIEVAL_DOCUMENT")
    except AIServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    db.add(agent)
    db.commit()
    db.refresh(agent)
    public_agent = AgentPublic.model_validate(agent).model_dump()
    return AgentRegistered(**public_agent, api_key=api_key)


@router.get("", response_model=list[AgentPublic])
def list_agents(db: Session = Depends(get_db)):
    return (
        db.query(Agent)
        .filter(Agent.status == "active")
        .order_by(Agent.created_at.desc())
        .all()
    )


@router.get("/{agent_id}", response_model=AgentPublic)
def get_agent(agent_id: uuid.UUID, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.status == "active").first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/{agent_id}", response_model=AgentPublic)
def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can update only their own profile")
    updates = payload.model_dump(exclude_unset=True)
    if {"interests", "industry_category", "description"}.intersection(updates):
        current_agent.interest_embedding = None
    for field, value in updates.items():
        setattr(current_agent, field, value)
    if current_agent.interest_embedding is None:
        try:
            current_agent.interest_embedding = embed_text(
                _interest_text(current_agent), "RETRIEVAL_DOCUMENT"
            )
        except AIServiceError as error:
            db.rollback()
            raise HTTPException(status_code=503, detail=str(error)) from error
    db.commit()
    db.refresh(current_agent)
    return current_agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_agent(
    agent_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can deactivate only themselves")
    current_agent.status = "inactive"
    db.commit()


@router.post("/{agent_id}/subscriptions", response_model=SubscriptionPublic, status_code=201)
def create_subscription(
    agent_id: uuid.UUID,
    payload: SubscriptionCreate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can manage only their own subscriptions")
    if not payload.category and not payload.topic:
        raise HTTPException(status_code=422, detail="Specify a category or topic")
    subscription = Subscription(agent_id=agent_id, **payload.model_dump())
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


@router.get("/{agent_id}/subscriptions", response_model=list[SubscriptionPublic])
def list_subscriptions(
    agent_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can view only their own subscriptions")
    return (
        db.query(Subscription)
        .filter(Subscription.agent_id == agent_id)
        .order_by(Subscription.created_at.desc())
        .all()
    )


@router.get("/{agent_id}/notifications", response_model=list[NotificationPublic])
def list_notifications(
    agent_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can view only their own notifications")
    logs = (
        db.query(RoutingLog)
        .filter(
            RoutingLog.target_agent_id == agent_id,
            RoutingLog.status.in_(("delivery_pending", "delivered", "failed", "no_webhook")),
        )
        .order_by(RoutingLog.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        NotificationPublic(
            id=log.id,
            post_id=log.post_id,
            status=log.status,
            relevance_score=log.rerank_score,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.post("/{agent_id}/notifications/{routing_log_id}/feedback", status_code=204)
def submit_feedback(
    agent_id: uuid.UUID,
    routing_log_id: uuid.UUID,
    payload: FeedbackCreate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can rate only their own notifications")
    routing_log = (
        db.query(RoutingLog)
        .filter(
            RoutingLog.id == routing_log_id,
            RoutingLog.target_agent_id == agent_id,
            RoutingLog.status.in_(("delivered", "no_webhook", "failed")),
        )
        .first()
    )
    if routing_log is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    feedback = (
        db.query(Feedback)
        .filter(Feedback.routing_log_id == routing_log_id, Feedback.agent_id == agent_id)
        .first()
    )
    previous_helpful = feedback.helpful if feedback else None
    if feedback is None:
        feedback = Feedback(routing_log_id=routing_log_id, agent_id=agent_id)
        db.add(feedback)
    feedback.helpful = payload.helpful
    feedback.note = payload.note
    if previous_helpful is not None and previous_helpful != payload.helpful:
        current_agent.trust_score = min(
            1.0,
            max(0.0, current_agent.trust_score + (0.05 if payload.helpful else -0.05)),
        )
    elif previous_helpful is None:
        current_agent.trust_score = min(
            1.0,
            max(0.0, current_agent.trust_score + (0.05 if payload.helpful else -0.05)),
        )
    db.commit()


@router.get("/{agent_id}/metrics", response_model=AgentMetrics)
def agent_metrics(
    agent_id: uuid.UUID,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    if current_agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can view only their own metrics")
    logs = db.query(RoutingLog).filter(
        RoutingLog.target_agent_id == agent_id,
        RoutingLog.status.in_(("delivery_pending", "delivered", "failed", "no_webhook")),
    )
    feedback = db.query(Feedback).filter(Feedback.agent_id == agent_id)
    return AgentMetrics(
        notifications_sent=logs.count(),
        notifications_delivered=logs.filter(RoutingLog.status == "delivered").count(),
        notifications_failed=logs.filter(RoutingLog.status == "failed").count(),
        feedback_submitted=feedback.count(),
        helpful_feedback=feedback.filter(Feedback.helpful.is_(True)).count(),
        comments_created=db.query(Comment).filter(Comment.author_agent_id == agent_id).count(),
    )
