"""Embeddings via Bedrock Titan text v2 (IAM auth, no API keys).

Best-effort by design: embedding failures must never block ingestion.
"""
from __future__ import annotations

import json

from pis.config import Settings

DIMENSIONS = 1024
MAX_CHARS = 8000


def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    """Embed texts with Titan v2. Raises on transport errors — callers decide
    whether that is fatal (backfill) or swallowed (ingest hook)."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
    vectors: list[list[float]] = []
    for text in texts:
        body = json.dumps({
            "inputText": text[:MAX_CHARS],
            "dimensions": DIMENSIONS,
            "normalize": True,
        })
        response = client.invoke_model(
            modelId=settings.embedding_model, body=body,
            accept="application/json", contentType="application/json",
        )
        vectors.append(json.loads(response["body"].read())["embedding"])
    return vectors


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in vec) + "]"
