import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import ROUTING_CANDIDATES, ROUTING_THRESHOLD
from app.models import Agent, Post, RoutingLog, Subscription
from app.services.embedding import embed_text
from app.services.reranking import rerank_post

logger = logging.getLogger(__name__)


def _post_text(post: Post) -> str:
    return f"{post.title}\n{post.content}\nCategory: {post.category}\nTopics: {post.topics}"


def route_post(db: Session, post_id: uuid.UUID) -> int:
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise LookupError(f"Post {post_id} does not exist")
    if post.routing_status == "completed":
        return (
            db.query(RoutingLog)
            .filter(RoutingLog.post_id == post.id, RoutingLog.status == "queued")
            .count()
        )
    if post.expires_at and post.expires_at <= datetime.now(timezone.utc):
        logger.info("Skipping expired post %s", post_id)
        post.routing_status = "completed"
        db.commit()
        return 0

    post_embedding = post.post_embedding
    if post_embedding is None:
        post_embedding = embed_text(_post_text(post), "RETRIEVAL_QUERY")
    post.post_embedding = post_embedding
    db.flush()

    relevant_values = {post.category.casefold()}
    relevant_values.update(
        topic.strip().casefold() for topic in post.topics.split(",") if topic.strip()
    )
    subscription_category_match = func.lower(Subscription.category).in_(relevant_values)
    subscription_topic_match = func.lower(Subscription.topic).in_(relevant_values)
    blocked_subscription = exists(
        select(Subscription.id).where(
            Subscription.agent_id == Agent.id,
            Subscription.blocked.is_(True),
            or_(subscription_category_match, subscription_topic_match),
        )
    )
    has_positive_subscription = exists(
        select(Subscription.id).where(
            Subscription.agent_id == Agent.id,
            Subscription.blocked.is_(False),
        )
    )
    matching_positive_subscription = exists(
        select(Subscription.id).where(
            Subscription.agent_id == Agent.id,
            Subscription.blocked.is_(False),
            or_(subscription_category_match, subscription_topic_match),
        )
    )
    distance = Agent.interest_embedding.cosine_distance(post_embedding).label("distance")
    candidates = (
        db.query(Agent, distance)
        .options(joinedload(Agent.subscriptions))
        .filter(
            Agent.status == "active",
            Agent.id != post.author_agent_id,
            Agent.interest_embedding.is_not(None),
            ~blocked_subscription,
            or_(~has_positive_subscription, matching_positive_subscription),
        )
        .order_by(distance)
        .limit(ROUTING_CANDIDATES)
        .all()
    )
    eligible = [(agent, float(1 - distance_value)) for agent, distance_value in candidates]
    if not eligible:
        post.routing_status = "completed"
        db.commit()
        return 0

    rerank_scores = rerank_post(
        {
            "title": post.title,
            "content": post.content,
            "category": post.category,
            "topics": post.topics,
        },
        [
            {
                "id": str(agent.id),
                "company": agent.company_name,
                "industry": agent.industry_category,
                "description": agent.description,
                "interests": agent.interests,
            }
            for agent, _ in eligible
        ],
    )

    routed = 0
    for agent, vector_score in eligible:
        raw_score = rerank_scores[str(agent.id)]
        rerank_score = raw_score * (0.9 + 0.1 * agent.trust_score)
        status = "queued" if rerank_score >= ROUTING_THRESHOLD else "below_threshold"
        existing = (
            db.query(RoutingLog)
            .filter(RoutingLog.post_id == post.id, RoutingLog.target_agent_id == agent.id)
            .first()
        )
        if existing:
            continue
        db.add(
            RoutingLog(
                post_id=post.id,
                target_agent_id=agent.id,
                vector_score=vector_score,
                rerank_score=rerank_score,
                status=status,
            )
        )
        routed += status == "queued"
    post.routing_status = "completed"
    post.routing_error = None
    db.commit()
    return routed


def get_routing_logs(db: Session, post_id: uuid.UUID) -> list[RoutingLog]:
    return list(
        db.scalars(
            select(RoutingLog)
            .where(RoutingLog.post_id == post_id)
            .order_by(RoutingLog.created_at.desc())
        )
    )