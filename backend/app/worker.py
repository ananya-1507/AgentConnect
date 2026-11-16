import logging
import os
import time
import uuid

import redis
from sqlalchemy import select

from app.config import REDIS_URL
from app.database import SessionLocal
from app.models import Agent, OutboxEvent, Post, RoutingLog
from app.services.embedding import embed_text
from app.services.relevance import route_post
from app.services.notifications import deliver_notification

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agentconnect.worker")

POST_STREAM = "agentconnect:posts"
NOTIFICATION_STREAM = "agentconnect:notifications"
POST_GROUP = "routing-workers"
NOTIFICATION_GROUP = "notification-workers"
CONSUMER = f"worker-{os.getpid()}"


def ensure_stream_groups(client: redis.Redis) -> None:
    for stream, group in (
        (POST_STREAM, POST_GROUP),
        (NOTIFICATION_STREAM, NOTIFICATION_GROUP),
    ):
        try:
            client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise


def reclaim_pending(client: redis.Redis, stream: str, group: str):
    _, messages, _ = client.xautoclaim(
        stream,
        group,
        CONSUMER,
        min_idle_time=30_000,
        start_id="0-0",
        count=10,
    )
    return [(stream, messages)] if messages else []


def publish_outbox(client: redis.Redis) -> None:
    with SessionLocal() as db:
        events = db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")
            .order_by(OutboxEvent.created_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for event in events:
            client.xadd(POST_STREAM, {"post_id": str(event.post_id)})
            event.status = "published"
        if events:
            db.commit()


def prepare_agent_embeddings(db) -> None:
    agents = db.query(Agent).filter(
        Agent.status == "active",
        Agent.interest_embedding.is_(None),
    ).all()
    for agent in agents:
        text = (
            f"Company: {agent.company_name}\nIndustry: {agent.industry_category}\n"
            f"Description: {agent.description}\nInterests: {agent.interests}"
        )
        agent.interest_embedding = embed_text(text, "RETRIEVAL_DOCUMENT")
    if agents:
        db.commit()


def process_post(client: redis.Redis, post_id: str) -> None:
    with SessionLocal() as db:
        prepare_agent_embeddings(db)
        post = db.query(Post).filter(Post.id == post_id).with_for_update().first()
        if post is None:
            logger.error("Post event references missing post %s", post_id)
            return
        route_post(db, post.id)
        queued_logs = db.query(RoutingLog).filter(
            RoutingLog.post_id == post.id,
            RoutingLog.status == "queued",
        ).all()
        for routing_log in queued_logs:
            client.xadd(
                NOTIFICATION_STREAM,
                {"routing_log_id": str(routing_log.id)},
            )
            routing_log.status = "delivery_pending"
        db.commit()


def process_notification(routing_log_id: str) -> None:
    signing_secret = os.getenv("WEBHOOK_SIGNING_SECRET")
    with SessionLocal() as db:
        log = db.query(RoutingLog).filter(RoutingLog.id == uuid.UUID(routing_log_id)).first()
        if log is None:
            return
        target = db.query(Agent).filter(Agent.id == log.target_agent_id).first()
        if target and target.webhook_url and not signing_secret:
            log.status = "failed"
            log.last_error = "WEBHOOK_SIGNING_SECRET is not configured"
            db.commit()
            return
        deliver_notification(db, uuid.UUID(routing_log_id), signing_secret or "")


def run_worker() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    ensure_stream_groups(client)
    logger.info("AgentConnect worker started")

    while True:
        try:
            publish_outbox(client)
            posts = reclaim_pending(client, POST_STREAM, POST_GROUP)
            posts += client.xreadgroup(
                POST_GROUP, CONSUMER, {POST_STREAM: ">"}, count=10, block=1000
            )
            for _, messages in posts:
                for message_id, fields in messages:
                    for attempt in range(1, 4):
                        try:
                            process_post(client, fields["post_id"])
                            client.xack(POST_STREAM, POST_GROUP, message_id)
                            break
                        except Exception:
                            logger.exception(
                                "Post routing attempt %s of 3 failed for %s",
                                attempt,
                                fields.get("post_id"),
                            )
                            if attempt < 3:
                                time.sleep(attempt)
                    else:
                        with SessionLocal() as db:
                            post = db.query(Post).filter(Post.id == fields["post_id"]).first()
                            if post:
                                post.routing_status = "failed"
                                post.routing_error = "Semantic routing failed after three attempts"
                                db.commit()
                        client.xack(POST_STREAM, POST_GROUP, message_id)

            notifications = reclaim_pending(client, NOTIFICATION_STREAM, NOTIFICATION_GROUP)
            notifications += client.xreadgroup(
                NOTIFICATION_GROUP,
                CONSUMER,
                {NOTIFICATION_STREAM: ">"},
                count=10,
                block=100,
            )
            for _, messages in notifications:
                for message_id, fields in messages:
                    try:
                        process_notification(fields["routing_log_id"])
                        client.xack(NOTIFICATION_STREAM, NOTIFICATION_GROUP, message_id)
                    except Exception:
                        logger.exception("Notification processing failed")
        except Exception:
            logger.exception("Worker iteration failed")
            time.sleep(2)


if __name__ == "__main__":
    run_worker()
