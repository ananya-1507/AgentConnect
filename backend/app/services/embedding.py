import logging

import httpx

from app.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, GOOGLE_API_KEY

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    pass


def embed_text(text: str, task_type: str) -> list[float]:
    if not GOOGLE_API_KEY:
        raise AIServiceError("GOOGLE_API_KEY is required for semantic relevance routing")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent"
    payload = {
        "model": f"models/{EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": task_type,
        "outputDimensionality": EMBEDDING_DIMENSIONS,
    }

    try:
        response = httpx.post(
            url,
            headers={"x-goog-api-key": GOOGLE_API_KEY},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        values = response.json()["embedding"]["values"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        logger.exception("Gemini embedding request failed")
        raise AIServiceError("Unable to generate a semantic embedding") from error

    if len(values) != EMBEDDING_DIMENSIONS:
        raise AIServiceError(
            f"Embedding service returned {len(values)} values; expected {EMBEDDING_DIMENSIONS}"
        )
    return values