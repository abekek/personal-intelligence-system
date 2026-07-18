"""Semantic/hybrid retrieval with a deterministic fake embedder."""
import hashlib
from pathlib import Path

from pis.config import Settings
from pis.embed_hook import make_embed_hook
from pis.embeddings import DIMENSIONS, to_pgvector
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.retrieval.search import search_fts, search_hybrid, search_semantic
from tests.test_normalize_chat import note_event

SETTINGS = Settings(embeddings_enabled=True)

# Deterministic fake embeddings: bucket by topic keyword so related texts
# cluster; orthogonal otherwise.
TOPICS = ["storage", "visa", "misc"]


def fake_embed(texts, settings):
    vectors = []
    for text in texts:
        topic = next((t for t in TOPICS if t in text.lower()), "misc")
        base = [0.0] * DIMENSIONS
        base[TOPICS.index(topic)] = 1.0
        jitter = int(hashlib.sha256(text.encode()).hexdigest()[:4], 16) / 65535 * 0.01
        base[10] = jitter
        vectors.append(base)
    return vectors


def seed(db):
    policy = PolicyEngine.load(Path("config"))
    hook = make_embed_hook(SETTINGS, embedder=fake_embed)
    events = [
        note_event(text="We chose gp3 STORAGE volumes for the database", key="a"),
        note_event(text="VISA interview scheduled for the petition", key="b"),
        note_event(text="Lunch plans for tuesday", key="c"),
    ]
    for ev in events:
        ingest_events(db, [ev], policy, embed_hook=hook)


def test_semantic_orders_by_topic_similarity(db):
    seed(db)
    query_vec = to_pgvector(fake_embed(["disk storage question"], SETTINGS)[0])
    hits = search_semantic(db, query_vec, limit=3)
    assert hits and "STORAGE" in hits[0].snippet


def test_hybrid_finds_semantic_match_fts_misses(db):
    seed(db)
    # No keyword overlap with the storage note ("disk" not in text) — FTS misses
    assert not any("STORAGE" in h.snippet for h in search_fts(db, "disk question"))
    query_vec = to_pgvector(fake_embed(["disk storage question"], SETTINGS)[0])
    hybrid = search_hybrid(db, "disk question", query_vec)
    assert any("STORAGE" in h.snippet for h in hybrid)


def test_hybrid_degrades_without_vector(db):
    seed(db)
    hits = search_hybrid(db, "visa interview petition", None)
    assert hits and "VISA" in hits[0].snippet


def test_embed_hook_failure_never_blocks_ingest(db):
    policy = PolicyEngine.load(Path("config"))

    def boom(texts, settings):
        raise RuntimeError("bedrock down")

    hook = make_embed_hook(SETTINGS, embedder=boom)
    [result] = ingest_events(db, [note_event(text="resilience check", key="z")],
                             policy, embed_hook=hook)
    assert result.status == "created"
    assert search_fts(db, "resilience check")
