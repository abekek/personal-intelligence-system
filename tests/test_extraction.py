import json
from pathlib import Path

import sqlalchemy as sa

from pis import ids
from pis.extraction.extractor import extract_conversation, parse_propositions
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from tests.test_normalize_chat import note_event
from tests.test_semantic_search import fake_embed, SETTINGS


def fake_llm_factory(payload):
    def llm(prompt):
        assert "[m0]" in prompt  # refs present
        return json.dumps(payload)
    return llm


def embedder(texts):
    return fake_embed(texts, SETTINGS)


def seed_conversation(db):
    policy = PolicyEngine.load(Path("config"))
    ingest_events(db, [note_event(
        text="We decided to use gp3 storage volumes for the ledger database",
        key="extract-me")], policy)
    return ids.conversation_id("claude", "extract-me")


def test_parse_propositions_salvage_and_validation():
    raw = 'Here you go:\n[{"kind": "decision", "statement": "Chose X", "confidence": 0.9, "evidence": ["m0"]}, {"kind": "nonsense", "statement": "drop me"}, {"statement": ""}]'
    props = parse_propositions(raw)
    assert len(props) == 1 and props[0]["kind"] == "decision"
    assert parse_propositions("no json here") == []


def test_extract_creates_items_with_evidence(db):
    conv_id = seed_conversation(db)
    llm = fake_llm_factory([{
        "kind": "decision",
        "statement": "Chose gp3 storage volumes for the ledger database",
        "confidence": 0.95, "evidence": ["m0"],
    }])
    counts = extract_conversation(db, conv_id, llm, embedder)
    assert counts == {"proposed": 1, "created": 1, "confirmed": 0}

    memory = db.execute(sa.text(
        "SELECT memory_id, kind, status, authority FROM memory_items")).first()
    assert memory[1] == "decision" and memory[2] == "current" and memory[3] == "observed"

    evidence = db.execute(sa.text(
        "SELECT event_id, excerpt FROM memory_evidence WHERE memory_id = :m"),
        {"m": memory[0]}).first()
    assert evidence[0].startswith("evt_")
    assert "gp3 storage" in evidence[1]

    extracted_at = db.execute(sa.text(
        "SELECT extracted_at FROM conversations WHERE id = :c"),
        {"c": conv_id}).scalar()
    assert extracted_at is not None


def test_reextraction_confirms_instead_of_duplicating(db):
    conv_id = seed_conversation(db)
    llm = fake_llm_factory([{
        "kind": "decision",
        "statement": "Chose gp3 storage volumes for the ledger database",
        "confidence": 0.95, "evidence": ["m0"],
    }])
    extract_conversation(db, conv_id, llm, embedder)
    counts = extract_conversation(db, conv_id, llm, embedder)
    assert counts["created"] == 0 and counts["confirmed"] == 1
    row = db.execute(sa.text(
        "SELECT count(*), max(authority) FROM memory_items")).first()
    assert row[0] == 1 and row[1] == "corroborated"


def test_memories_surface_in_search_and_context_pack(db):
    from pis.embeddings import to_pgvector
    from pis.retrieval.search import build_context_pack, search_fts

    conv_id = seed_conversation(db)
    llm = fake_llm_factory([{
        "kind": "decision",
        "statement": "Chose gp3 storage volumes for the ledger database",
        "confidence": 0.95, "evidence": ["m0"],
    }])
    extract_conversation(db, conv_id, llm, embedder)

    hits = search_fts(db, "gp3 storage volumes ledger")
    assert any(h.kind == "memory" for h in hits)

    vec = to_pgvector(embedder(["storage decision for the database"])[0])
    pack = build_context_pack(db, "storage decision for the database", vec)
    assert pack["memories"], "pack should surface the decision"
    memory = pack["memories"][0]
    assert memory["kind"] == "decision"
    assert memory["evidence_event_ids"] and memory["evidence_event_ids"][0].startswith("evt_")


def test_empty_conversation_marks_extracted(db):
    # conversation with no extractable content still advances the cursor
    conv_id = seed_conversation(db)
    counts = extract_conversation(db, conv_id, fake_llm_factory([]), embedder)
    assert counts == {"proposed": 0, "created": 0, "confirmed": 0}
