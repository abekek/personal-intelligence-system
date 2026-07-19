import json
from pathlib import Path

import sqlalchemy as sa

import pis.normalize.chat  # noqa: F401
import pis.normalize.claude_code  # noqa: F401
from pis import ids
from pis.extraction.extractor import extract_conversation, parse_propositions
from pis.extraction import hygiene
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from tests.test_normalize_chat import note_event
from tests.test_normalize_claude_code import turn_event
from tests.test_semantic_search import fake_embed, SETTINGS


def make_llm(extraction_payload, relations=None):
    """Fake LLM serving both the extraction and contradiction prompts."""
    def llm(prompt):
        if "EXISTING:" in prompt:
            return json.dumps([{"pair": i, "relation": r}
                               for i, r in enumerate(relations or [])])
        return json.dumps(extraction_payload)
    return llm


def embedder(texts):
    return fake_embed(texts, SETTINGS)


def policy():
    return PolicyEngine.load(Path("config"))


def seed_transcript(db, prompt="Discussing database storage options",
                    answer="Went with gp3 storage volumes"):
    ingest_events(db, [turn_event(prompt=prompt, answer=answer)], policy())
    return ids.conversation_id("claude_code", "sess-abc")


PROP = {"kind": "decision",
        "statement": "Chose gp3 STORAGE volumes for the ledger database",
        "confidence": 0.95, "evidence": ["m0", "m0", "m1"]}


def test_parse_propositions_salvage_and_validation():
    raw = 'ok:\n[{"kind": "decision", "statement": "Chose X", "confidence": 0.9, "evidence": ["m0"]}, {"kind": "junk", "statement": "drop"}]'
    assert len(parse_propositions(raw)) == 1
    assert parse_propositions("nope") == []


def test_extract_creates_items_with_deduped_evidence(db):
    conv_id = seed_transcript(db)
    counts = extract_conversation(db, conv_id, make_llm([PROP]), embedder)
    assert counts["created"] == 1 and counts["skipped_notes"] == 0

    memory_id, authority = db.execute(sa.text(
        "SELECT memory_id, authority FROM memory_items")).first()
    assert authority == "observed"  # transcript-grounded
    evidence = db.execute(sa.text(
        "SELECT event_id FROM memory_evidence WHERE memory_id = :m"),
        {"m": memory_id}).fetchall()
    # m0+m0+m1 refs collapse to ONE evidence row: both messages of a turn
    # share the same ledger event, and evidence is per-event
    assert len(evidence) == 1


def test_note_only_conversations_are_skipped(db):
    ingest_events(db, [note_event(text="A note asserting grand progress",
                                  key="notes-stream")], policy())
    conv_id = ids.conversation_id("claude", "notes-stream")
    counts = extract_conversation(db, conv_id, make_llm([PROP]), embedder)
    assert counts["skipped_notes"] == 1 and counts["proposed"] == 0
    assert db.execute(sa.text("SELECT count(*) FROM memory_items")).scalar() == 0


def test_supersession_at_extraction(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "Considering STORAGE options for the paper draft",
        "confidence": 0.95, "evidence": ["m0"]}]), embedder)

    # force re-extraction of the same conversation with an updated state
    db.execute(sa.text("UPDATE conversations SET extracted_at = NULL"))
    db.commit()
    # fake embedder: same 'storage' topic bucket but different jitter -> sim
    # lands between CONFLICT and MERGE thresholds
    counts = extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "STORAGE finalized and submitted for the draft",
        "confidence": 0.8, "evidence": ["m1"]}], relations=["supersedes"]), embedder)
    assert counts["superseded"] == 1

    rows = dict(db.execute(sa.text(
        "SELECT statement, status FROM memory_items")).fetchall())
    assert rows["Considering STORAGE options for the paper draft"] == "superseded"
    assert rows["STORAGE finalized and submitted for the draft"] == "current"

    new_row = db.execute(sa.text(
        "SELECT supersedes_memory_id FROM memory_items WHERE status = 'current'")).scalar()
    assert new_row is not None


def test_pack_excludes_superseded_and_asserted(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "Old STORAGE state", "confidence": 0.99,
        "evidence": ["m0"]}]), embedder)
    db.execute(sa.text("UPDATE conversations SET extracted_at = NULL"))
    db.commit()
    extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "New STORAGE state achieved", "confidence": 0.5,
        "evidence": ["m1"]}], relations=["supersedes"]), embedder)
    db.execute(sa.text(
        "UPDATE memory_items SET authority = 'asserted' "
        "WHERE statement = 'New STORAGE state achieved'"))
    db.commit()

    from pis.embeddings import to_pgvector
    from pis.retrieval.search import build_context_pack
    vec = to_pgvector(embedder(["storage state"])[0])
    pack = build_context_pack(db, "storage state", vec)
    statements = [m["statement"] for m in pack["memories"]]
    assert "Old STORAGE state" not in statements          # superseded
    assert "New STORAGE state achieved" not in statements  # asserted


def test_hygiene_merge_and_retract(db):
    from pis.embeddings import to_pgvector
    seed_transcript(db)
    # two near-identical memories inserted directly (as the pre-fix extractor
    # would have), with staggered timestamps so a survivor is well-defined
    for i, statement in enumerate(("Preparing the STORAGE submission now",
                                   "Preparing the STORAGE submission right now")):
        db.execute(sa.text("""
            INSERT INTO memory_items (memory_id, kind, statement, status,
                authority, confidence, first_observed_at, last_confirmed_at,
                sensitivity, embedding)
            VALUES (:m, 'task', :s, 'current', 'observed', 0.9,
                now() + (:i || ' seconds')::interval,
                now() + (:i || ' seconds')::interval,
                'confidential-personal', CAST(:v AS vector))
        """), {"m": f"mem_dupe{i}", "s": statement, "i": i,
               "v": to_pgvector(embedder([statement])[0])})
    db.commit()

    # a note-only-evidenced memory, inserted manually
    ingest_events(db, [note_event(text="asserted claim", key="n2")], policy())
    note_event_id = db.execute(sa.text(
        "SELECT event_id FROM events WHERE capture_method = 'manual'")).scalar()
    db.execute(sa.text("""
        INSERT INTO memory_items (memory_id, kind, statement, status, authority,
            confidence, first_observed_at, last_confirmed_at, sensitivity)
        VALUES ('mem_noteonly', 'fact', 'Note-derived fact', 'current', 'observed',
            0.9, now(), now(), 'confidential-personal')"""))
    db.execute(sa.text(
        "INSERT INTO memory_evidence (id, memory_id, event_id) "
        "VALUES ('evi_x', 'mem_noteonly', :e)"), {"e": note_event_id})
    db.commit()

    assert hygiene.retract_note_only(db) == 1
    result = hygiene.merge_batch(db)
    assert result["merged"] >= 1
    current = db.execute(sa.text(
        "SELECT count(*) FROM memory_items WHERE status = 'current' "
        "AND statement LIKE 'Preparing%'")).scalar()
    assert current == 1


def test_hygiene_evidence_dedup(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([PROP]), embedder)
    memory_id = db.execute(sa.text("SELECT memory_id FROM memory_items")).scalar()
    event_id = db.execute(sa.text(
        "SELECT event_id FROM memory_evidence LIMIT 1")).scalar()
    db.execute(sa.text(
        "INSERT INTO memory_evidence (id, memory_id, event_id) "
        "VALUES ('evi_dup', :m, :e)"), {"m": memory_id, "e": event_id})
    db.commit()
    assert hygiene.dedup_evidence(db) == 1
