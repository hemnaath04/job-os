"""Embeddings — currently a no-op.

This project uses Claude directly as the retriever (the M3 tailoring agent
passes the whole profile to Opus 4.8). The `embedding` column on
`fact_bullets` stays NULL; left in the schema for future Voyage/Anthropic
embeddings if profile size ever justifies vector search.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

EMBEDDING_DIM = 1536  # column dimension only; nothing populated today


async def embed_many(texts: list[str]) -> list[list[float] | None]:
    if texts:
        log.debug("embeddings.noop", count=len(texts))
    return [None] * len(texts)


async def embed_one(_text: str) -> list[float] | None:
    return None
