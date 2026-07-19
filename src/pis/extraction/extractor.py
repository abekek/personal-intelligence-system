"""Memory extraction: conversations -> durable propositions with provenance.

An LLM (Claude on Bedrock, IAM auth) reads a conversation and proposes
memory items; each cites the messages that support it, which we resolve to
immutable ledger event ids. Near-duplicate propositions (embedding cosine)
confirm existing items instead of inserting twice.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from pis.config import Settings

PROMPT_VERSION = "v2"
MAX_CONTEXT_CHARS = 12000
KINDS = {"fact", "decision", "claim", "result", "task", "open_question",
         "preference", "risk"}
# >= MERGE: same proposition — confirm existing + union evidence.
# [CONFLICT, MERGE): same topic — ask the LLM whether the new one supersedes.
MERGE_SIMILARITY = 0.86
CONFLICT_SIMILARITY = 0.70

CONTRADICTION_PROMPT = """You maintain a personal memory store. For each pair below, decide the
relation between an EXISTING memory and a NEW candidate memory about the same topic.

Relations:
- "supersedes": the NEW statement describes a later state of the same thing (progress, changed
  decision, updated status) — the EXISTING one is now stale.
- "duplicate": both describe the SAME proposition (same activity/fact, merely reworded or with
  minor detail differences) — keep one.
- "distinct": both can be true simultaneously (different aspects, both worth keeping).

Return STRICT JSON, no prose: [{"pair": <index>, "relation": "supersedes|duplicate|distinct"}]

Pairs:
{pairs}
"""
MAX_WINDOWS = 6

PROMPT = """You are extracting durable memory from a conversation for a personal knowledge system.

Below is a conversation. Each message is tagged with a ref like [m3].

Extract ONLY durable, self-contained propositions worth remembering months later:
- decisions made and their reasons
- concrete facts about the user's work, projects, or life
- results/outcomes (numbers, acceptances, failures)
- open questions or tasks explicitly left unresolved
- stated preferences or constraints

Skip: chit-chat, transient details, restatements of the question, anything a
search of the raw conversation would answer better than a summary.

Return STRICT JSON — an array (possibly empty), no prose:
[{"kind": "fact|decision|claim|result|task|open_question|preference|risk",
  "statement": "<one self-contained sentence, past tense, with concrete names/numbers>",
  "confidence": 0.0-1.0,
  "evidence": ["m3", "m7"]}]

Conversation (title: {title}):
{body}
"""


def build_windows(title: str, messages: list[dict]) -> list[tuple[str, dict[str, str]]]:
    """Split the conversation into consecutive <=MAX_CONTEXT_CHARS windows so
    long sessions are mined in full, not tail-sampled. Returns up to
    MAX_WINDOWS (prompt, ref->event_id) pairs, keeping the NEWEST windows
    when capped."""
    windows: list[list[dict]] = [[]]
    total = 0
    for message in messages:
        line_len = len(message["text"][:2000]) + 20
        if total + line_len > MAX_CONTEXT_CHARS and windows[-1]:
            windows.append([])
            total = 0
        windows[-1].append(message)
        total += line_len
    windows = [w for w in windows if w][-MAX_WINDOWS:]

    out = []
    for window in windows:
        ref_map = {m["ref"]: m["event_id"] for m in window}
        body = "\n".join(
            f"[{m['ref']}] {m['role']}: {m['text'][:2000]}" for m in window)
        prompt = PROMPT.replace("{title}", title or "untitled").replace("{body}", body)
        out.append((prompt, ref_map))
    return out


def bedrock_llm(settings: Settings):
    import boto3
    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)

    def call(prompt: str) -> str:
        response = client.converse(
            modelId=settings.extraction_model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2000},
        )
        return response["output"]["message"]["content"][0]["text"]
    return call


def parse_propositions(raw: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        kind = str(item.get("kind", "fact"))
        if not statement or kind not in KINDS:
            continue
        out.append({
            "kind": kind, "statement": statement,
            "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
            "evidence": [str(e) for e in (item.get("evidence") or [])],
        })
    return out


def conversation_messages(db: Session, conversation_id: str) -> list[dict]:
    rows = db.execute(sa_text("""
        SELECT m.role, r.text_content, r.event_id, e.capture_method
        FROM messages m
        JOIN message_revisions r ON r.message_id = m.id
        JOIN events e ON e.event_id = r.event_id
        WHERE m.conversation_id = :cid ORDER BY m.position
    """), {"cid": conversation_id})
    return [{"ref": f"m{i}", "role": role, "text": content or "",
             "event_id": event_id, "capture_method": capture_method}
            for i, (role, content, event_id, capture_method) in enumerate(rows)]


def _insert_evidence(db: Session, memory_id: str, refs: list[str],
                     ref_map: dict, messages: list[dict]) -> None:
    for ref in dict.fromkeys(refs):  # de-duplicated, order preserved
        event_id = ref_map.get(ref)
        if not event_id:
            continue
        exists = db.execute(sa_text(
            "SELECT 1 FROM memory_evidence WHERE memory_id = :m AND event_id = :e"),
            {"m": memory_id, "e": event_id}).first()
        if exists:
            continue
        excerpt = next((m["text"][:300] for m in messages if m["ref"] == ref), None)
        db.execute(sa_text(
            "INSERT INTO memory_evidence (id, memory_id, event_id, excerpt) "
            "VALUES (:id, :mid, :eid, :ex)"),
            {"id": "evi_" + uuid.uuid4().hex[:16], "mid": memory_id,
             "eid": event_id, "ex": excerpt})


def classify_pairs(llm, pairs: list[tuple[str, str]]) -> list[str]:
    """pairs: [(existing_statement, new_statement)] -> relations list."""
    if not pairs:
        return []
    body = "\n".join(
        f'{i}. EXISTING: "{old}"\n   NEW: "{new}"'
        for i, (old, new) in enumerate(pairs)
    )
    raw = llm(CONTRADICTION_PROMPT.replace("{pairs}", body))
    relations = ["distinct"] * len(pairs)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            for verdict in json.loads(match.group(0)):
                index = int(verdict.get("pair", -1))
                relation = verdict.get("relation")
                if 0 <= index < len(pairs) and relation in ("supersedes", "duplicate"):
                    relations[index] = relation
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return relations


def _find_similar(db: Session, vec_literal: str) -> tuple[str, float] | None:
    row = db.execute(sa_text("""
        SELECT memory_id, 1 - (embedding <=> CAST(:v AS vector)) AS sim
        FROM memory_items WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1
    """), {"v": vec_literal}).first()
    return (row[0], float(row[1])) if row else None


def extract_conversation(db: Session, conversation_id: str, llm, embedder,
                         run_id: str | None = None) -> dict:
    """Returns {proposed, created, confirmed, superseded, skipped_notes}."""
    from pis.embeddings import to_pgvector

    messages = conversation_messages(db, conversation_id)
    counts = {"proposed": 0, "created": 0, "confirmed": 0,
              "superseded": 0, "skipped_notes": 0}
    now = datetime.now(timezone.utc)

    # Capture-note streams already ARE curated memory statements — mining
    # them again would launder assertions into observed facts.
    all_manual = bool(messages) and all(
        m["capture_method"] == "manual" for m in messages)
    if all_manual:
        counts["skipped_notes"] = 1
        messages = []

    if messages:
        row = db.execute(sa_text("""
            SELECT c.title, c.sensitivity,
                   regexp_replace(s.repo_root, '.*/', '') AS repo
            FROM conversations c
            LEFT JOIN code_sessions s ON s.session_id = c.provider_conversation_id
            WHERE c.id = :cid"""),
            {"cid": conversation_id}).first()
        title, conv_sensitivity, project_id = (
            (row[0] or "", row[1], row[2]) if row
            else ("", "confidential-personal", None))
        propositions = []
        ref_map: dict[str, str] = {}
        for window_prompt, window_refs in build_windows(title, messages):
            propositions.extend(parse_propositions(llm(window_prompt)))
            ref_map.update(window_refs)
        counts["proposed"] = len(propositions)
        vectors = embedder([p["statement"] for p in propositions]) if propositions else []

        pending: list[dict] = []       # inserted after conflict classification
        conflict_pairs: list[tuple[str, str]] = []
        for proposition, vec in zip(propositions, vectors):
            vec_literal = to_pgvector(vec)
            similar = _find_similar(db, vec_literal)
            if similar and similar[1] >= MERGE_SIMILARITY:
                db.execute(sa_text(
                    "UPDATE memory_items SET last_confirmed_at = :now, "
                    "authority = CASE WHEN authority = 'observed' THEN 'corroborated' "
                    "ELSE authority END WHERE memory_id = :mid"),
                    {"now": now, "mid": similar[0]})
                _insert_evidence(db, similar[0], proposition["evidence"], ref_map, messages)
                counts["confirmed"] += 1
                continue
            entry = {"proposition": proposition, "vec": vec_literal,
                     "conflict_with": None}
            if similar and similar[1] >= CONFLICT_SIMILARITY:
                old_statement = db.execute(sa_text(
                    "SELECT statement FROM memory_items WHERE memory_id = :m "
                    "AND status = 'current'"), {"m": similar[0]}).scalar()
                if old_statement:
                    entry["conflict_with"] = similar[0]
                    conflict_pairs.append((old_statement, proposition["statement"]))
            pending.append(entry)

        relations = classify_pairs(llm, conflict_pairs)
        relation_iter = iter(relations)
        for entry in pending:
            proposition = entry["proposition"]
            supersedes_id = None
            if entry["conflict_with"] is not None:
                relation = next(relation_iter, "distinct")
                if relation == "duplicate":
                    # same proposition, reworded — confirm + union evidence
                    db.execute(sa_text(
                        "UPDATE memory_items SET last_confirmed_at = :now, "
                        "authority = CASE WHEN authority = 'observed' THEN "
                        "'corroborated' ELSE authority END WHERE memory_id = :mid"),
                        {"now": now, "mid": entry["conflict_with"]})
                    _insert_evidence(db, entry["conflict_with"],
                                     proposition["evidence"], ref_map, messages)
                    counts["confirmed"] += 1
                    continue
                if relation == "supersedes":
                    supersedes_id = entry["conflict_with"]
            evidence_events = [ref_map.get(r) for r in proposition["evidence"]
                               if ref_map.get(r)]
            manual_only = bool(evidence_events) and all(
                next((m["capture_method"] for m in messages
                      if m["event_id"] == eid), "") == "manual"
                for eid in evidence_events)
            authority = "asserted" if manual_only else "observed"

            memory_id = "mem_" + uuid.uuid4().hex[:16]
            db.execute(sa_text("""
                INSERT INTO memory_items (memory_id, kind, statement, project_id,
                    status, authority, confidence, first_observed_at,
                    last_confirmed_at, sensitivity, supersedes_memory_id,
                    extraction_run_id, source_conversation_id, embedding)
                VALUES (:mid, :kind, :stmt, :proj, 'current', :auth, :conf,
                    :now, :now, :sens, :sup, :run, :cid, CAST(:vec AS vector))
            """), {"mid": memory_id, "kind": proposition["kind"],
                   "proj": project_id,
                   "stmt": proposition["statement"], "auth": authority,
                   "conf": proposition["confidence"], "now": now,
                   "sens": conv_sensitivity, "sup": supersedes_id,
                   "run": run_id, "cid": conversation_id, "vec": entry["vec"]})
            if supersedes_id:
                db.execute(sa_text(
                    "UPDATE memory_items SET status = 'superseded' "
                    "WHERE memory_id = :m"), {"m": supersedes_id})
                counts["superseded"] += 1
            _insert_evidence(db, memory_id, proposition["evidence"], ref_map, messages)
            counts["created"] += 1
    db.execute(sa_text(
        "UPDATE conversations SET extracted_at = :now WHERE id = :cid"),
        {"now": now, "cid": conversation_id})
    db.commit()
    return counts
