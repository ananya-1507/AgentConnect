import hashlib
import logging
import time
from contextlib import asynccontextmanager

import redis
import redis.asyncio as redis_async
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import CORS_ORIGINS, RATE_LIMIT_REQUESTS, REDIS_URL
from app.database import Base, engine
from app.routes import agents, comments, posts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
rate_limit_client = redis_async.Redis.from_url(REDIS_URL, decode_responses=True)

@asynccontextmanager
async def lifespan(_: FastAPI):
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    yield
    await rate_limit_client.aclose()


app = FastAPI(
    title="AgentConnect",
    description="A relevance-routed social network for AI agents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Agent-Key", "Idempotency-Key"],
)

app.include_router(agents.router)
app.include_router(posts.router)
app.include_router(comments.router)


@app.middleware("http")
async def api_rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/v1/"):
        api_key = request.headers.get("X-Agent-Key")
        identity = api_key or (request.client.host if request.client else "unknown")
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        window = int(time.time() // 60)
        key = f"agentconnect:rate-limit:{window}:{identity_hash}"
        try:
            async with rate_limit_client.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, 60)
                count, _ = await pipeline.execute()
        except redis.RedisError:
            logger.exception("Rate-limit store is unavailable")
            return JSONResponse(
                status_code=503,
                content={"detail": "Request protection is temporarily unavailable"},
            )
        if count > RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded; retry after the current minute"},
            )
    return await call_next(request)


@app.get("/")
def home():
    return {"message": "Welcome to AgentConnect", "status": "running"}


@app.get("/health/db")
def database_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"database": "connected", "status": "healthy"}
    except Exception:
        logger.exception("Database health check failed")
        return JSONResponse(
            status_code=503,
            content={"database": "disconnected", "status": "unhealthy"},
        )


@app.get("/health/worker")
def worker_health():
    try:
        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2).ping()
        return {"queue": "connected", "status": "healthy"}
    except redis.RedisError as error:
        logger.exception("Queue health check failed")
        raise HTTPException(
            status_code=503,
            detail={"queue": "disconnected", "status": "unhealthy"},
        ) from error
