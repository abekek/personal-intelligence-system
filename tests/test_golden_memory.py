"""Golden memory evaluations: the 2026-07-18/19 QA episode frozen as
regression tests. Each test encodes one acceptance criterion; a change to
extraction, hygiene, or pack serving that reintroduces a failure mode fails
here first. (These are also the embryo of the paper's benchmark metrics.)"""
import sqlalchemy as sa

import pis.normalize.chat  # noqa: F401
import pis.normalize.claude_code  # noqa: F401
from pathlib import Path

from pis.config import Settings
from pis.curation import confirm_memory, correct_memory, retract_memory
from pis.embeddings import to_pgvector, DIMENSIONS
from pis.extraction.extractor import extract_conversation
from pis.policy.engine import PolicyEngine
from pis.retrieval.search import build_context_pack
from tests.test_extraction import embedder, make_llm, seed_transcript
from tests.test_normalize_chat import note_event
from tests.test_semantic_search import fake_embed, SETTINGS


def policy():
    return PolicyEngine.load(Path("config"))


def vec_for(text):
    return to_pgvector(embedder([text])[0])


def pack_statements(db, topic):
    pack = build_context_pack(db, topic, vec_for(topic))
    return [m["statement"] for m in pack["memories"]]


def reextract(db, conv_id, llm):
    db.execute(sa.text("UPDATE conversations SET extracted_at = NULL"))
    db.commit()
    return extract_conversation(db, conv_id, llm, embedder)


# G1 — staleness inversion: superseded state must not be served, regardless
# of the stale memory's higher confidence.
def test_golden_staleness_inversion(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "Considering STORAGE options for the draft",
        "confidence": 0.95, "evidence": ["m0"]}]), embedder)
    reextract(db, conv_id, make_llm([{
        "kind": "fact", "statement": "STORAGE submitted and confirmed for the draft",
        "confidence": 0.6, "evidence": ["m1"]}], relations=["supersedes"]))
    statements = pack_statements(db, "storage state of the draft")
    assert "Considering STORAGE options for the draft" not in statements
    assert "STORAGE submitted and confirmed for the draft" in statements


# G2 — provenance laundering: agent-note streams are never mined; a memory
# whose only evidence is a capture note never reaches a pack.
def test_golden_provenance_laundering(db):
    ingest_events_notes(db)
    conv_id = ids_conv("claude", "notes-g2")
    counts = extract_conversation(
        db, conv_id, make_llm([{
            "kind": "fact", "statement": "Grand STORAGE milestone achieved",
            "confidence": 0.99, "evidence": ["m0"]}]), embedder)
    assert counts["skipped_notes"] == 1 and counts["created"] == 0
    assert pack_statements(db, "storage milestone") == []


def ingest_events_notes(db):
    from pis.ingest.service import ingest_events
    ingest_events(db, [note_event(
        text="Agent note claiming a grand STORAGE milestone achieved",
        key="notes-g2")], policy())


def ids_conv(provider, key):
    from pis import ids
    return ids.conversation_id(provider, key)


# G3 — duplicate accretion: paraphrase in the conflict band with a
# "duplicate" verdict must confirm, not create a variant.
def test_golden_duplicate_collapse(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([{
        "kind": "task", "statement": "Preparing the STORAGE submission package",
        "confidence": 0.9, "evidence": ["m0"]}]), embedder)
    reextract(db, conv_id, make_llm([{
        "kind": "task", "statement": "Working through STORAGE submission preparation",
        "confidence": 0.9, "evidence": ["m1"]}], relations=["duplicate"]))
    current = db.execute(sa.text(
        "SELECT count(*) FROM memory_items WHERE status = 'current'")).scalar()
    assert current == 1


# G4 — sensitivity topic bleed: at moderate topic similarity (0.45-0.60),
# ordinary memories surface but highly-sensitive ones require closer match.
def test_golden_sensitivity_floor(db):
    axis = [0.0] * DIMENSIONS
    axis[0] = 1.0
    query = [0.0] * DIMENSIONS
    query[0] = 0.5
    query[1] = 0.75 ** 0.5  # cos(query, axis) = 0.5
    for memory_id, sensitivity in (("mem_g4_plain", "confidential-personal"),
                                   ("mem_g4_sensitive", "highly-sensitive")):
        db.execute(sa.text("""
            INSERT INTO memory_items (memory_id, kind, statement, status,
                authority, confidence, first_observed_at, last_confirmed_at,
                sensitivity, embedding)
            VALUES (:m, 'fact', :s, 'current', 'observed', 0.9, now(), now(),
                    :sens, CAST(:v AS vector))
        """), {"m": memory_id, "s": f"Fact held by {memory_id}",
               "sens": sensitivity, "v": to_pgvector(axis)})
    db.commit()
    pack = build_context_pack(db, "zzz nolexicalmatch zzz", to_pgvector(query))
    ids_in_pack = [m["memory_id"] for m in pack["memories"]]
    assert "mem_g4_plain" in ids_in_pack          # 0.5 >= 0.45 floor
    assert "mem_g4_sensitive" not in ids_in_pack  # 0.5 < 0.60 sensitive floor


# G5 — curation: corrections supersede and outrank; retraction hides;
# nothing is deleted.
def test_golden_curation_flow(db):
    conv_id = seed_transcript(db)
    extract_conversation(db, conv_id, make_llm([{
        "kind": "fact", "statement": "The STORAGE panel meets on Tuesdays",
        "confidence": 0.9, "evidence": ["m0"]}]), embedder)
    memory_id = db.execute(sa.text(
        "SELECT memory_id FROM memory_items")).scalar()

    confirmed = confirm_memory(db, memory_id)
    assert confirmed["authority"] == "user_confirmed"

    corrected = correct_memory(db, SETTINGS, policy(), memory_id,
                               "The STORAGE panel meets on Thursdays",
                               note="user correction", embedder=embedder)
    assert corrected["authority"] == "user_confirmed"
    statements = pack_statements(db, "storage panel meeting day")
    assert "The STORAGE panel meets on Thursdays" in statements
    assert "The STORAGE panel meets on Tuesdays" not in statements
    # the correction carries ledger evidence
    evidence = db.execute(sa.text(
        "SELECT count(*) FROM memory_evidence WHERE memory_id = :m"),
        {"m": corrected["memory_id"]}).scalar()
    assert evidence >= 1

    retract_memory(db, corrected["memory_id"], reason="golden test")
    assert "The STORAGE panel meets on Thursdays" not in pack_statements(
        db, "storage panel meeting day")
    # nothing deleted: all three memory rows still exist
    total = db.execute(sa.text("SELECT count(*) FROM memory_items")).scalar()
    assert total == 2
