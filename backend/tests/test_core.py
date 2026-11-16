import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://agentconnect:agentconnect@localhost:5432/agentconnect",
)

import json
from unittest.mock import Mock

import pytest

from app.schemas import AgentCreate
from app.security import sign_webhook
from app.services.embedding import AIServiceError
from app.services.notifications import verify_webhook_signature
from app.services.reranking import rerank_post


def test_webhook_signatures_are_verifiable_and_body_bound():
    body = b'{"event":"post.relevant"}'
    signature = f"sha256={sign_webhook('test-secret', body)}"

    assert verify_webhook_signature("test-secret", body, signature)
    assert not verify_webhook_signature("wrong-secret", body, signature)
    assert not verify_webhook_signature("test-secret", body + b" ", signature)


def test_agent_webhook_requires_https():
    with pytest.raises(ValueError):
        AgentCreate(
            company_name="Example Co",
            industry_category="payments",
            interests="UPI and payment processing",
            webhook_url="http://example.com/hook",
        )

    agent = AgentCreate(
        company_name="Example Co",
        industry_category="payments",
        interests="UPI and payment processing",
        webhook_url="https://example.com/hook",
    )
    assert agent.webhook_url == "https://example.com/hook"


def test_reranker_returns_scores_for_every_candidate(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "scores": [
                                                {"id": "phonepe", "score": 0.9},
                                                {"id": "bajaj", "score": 0.1},
                                            ]
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.services.reranking.GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.services.reranking.httpx.post",
        Mock(return_value=FakeResponse()),
    )
    scores = rerank_post(
        {"title": "Festive food offer", "content": "Food delivery offer"},
        [
            {"id": "phonepe", "company": "PhonePe", "interests": "payments"},
            {"id": "bajaj", "company": "Bajaj Auto", "interests": "vehicles"},
        ],
    )

    assert scores == {"phonepe": 0.9, "bajaj": 0.1}


def test_reranker_rejects_incomplete_candidate_scores(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '{"scores":[{"id":"phonepe","score":0.9}]}'}]
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.services.reranking.GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.services.reranking.httpx.post",
        Mock(return_value=FakeResponse()),
    )
    with pytest.raises(AIServiceError, match="did not score every candidate"):
        rerank_post(
            {"title": "Festive food offer", "content": "Food delivery offer"},
            [
                {"id": "phonepe", "company": "PhonePe", "interests": "payments"},
                {"id": "bajaj", "company": "Bajaj Auto", "interests": "vehicles"},
            ],
        )
