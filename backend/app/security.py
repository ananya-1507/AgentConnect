import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent


def create_api_key() -> tuple[str, str]:
    api_key = secrets.token_urlsafe(32)
    return api_key, hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_current_agent(
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    db: Session = Depends(get_db),
) -> Agent:
    if not x_agent_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provide an agent API key in the X-Agent-Key header",
        )
    digest = hashlib.sha256(x_agent_key.encode("utf-8")).hexdigest()
    agent = db.query(Agent).filter(Agent.api_key_hash == digest).first()
    if agent is None or not hmac.compare_digest(agent.api_key_hash, digest):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent API key")
    if agent.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is inactive")
    return agent


def sign_webhook(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
