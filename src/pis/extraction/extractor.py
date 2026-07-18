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

PROMPT_VERSION = "v1"
MAX_CONTEXT_CHARS = 12000
KINDS = {"fact", "decision", "claim", "result", "task", "open_question",
         "preference", "risk"}
DEDUP_SIMILARITY = 0.90

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


def build_prompt(title: str, messages: list[dict]) -> tuple[str, dict[str, str]]:
    """messages: [{ref, role, text, event_id}] newest-last. Returns prompt and
    ref->event_id map. Caps context to the most recent MAX_CONTEXT_CHARS."""
    ref_map: dict[str, str] = {}
    lines: list[str] = []
    total = 0
    for message in reversed(messages):
        line = f"[{message['ref']}] {message['role']}: {message['text'][:2000]}"
        if total + len(line) > MAX_CONTEXT_CHARS:
            break
        lines.append(line)
        ref_map[message["ref"]] = message["event_id"]
        total += len(line)
    body = "\n".join(reversed(lines))
    return PROMPT.replace("{title}", title or "untitled").replace("{body}", body), ref_map


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
        SELECT m.role, r.text_content, r.event_id
        FROM messages m JOIN message_revisions r ON r.message_id = m.id
        WHERE m.conversation_id = :cid ORDER BY m.position
    """), {"cid": conversation_id})
    return [{"ref": f"m{i}", "role": role, "text": content or "", "event_id": event_id}
            for i, (role, content, event_id) in enumerate(rows)]


def _find_similar(db: Session, vec_literal: str) -> tuple[str, float] | None:
    row = db.execute(sa_text("""
        SELECT memory_id, 1 - (embedding <=> CAST(:v AS vector)) AS sim
        FROM memory_items WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1
    """), {"v": vec_literal}).first()
    return (row[0], float(row[1])) if row else None


def extract_conversation(db: Session, conversation_id: str, llm, embedder,
                         run_id: str | None = None) -> dict:
    """Returns {proposed, created, confirmed}."""
    from pis.embeddings import to_pgvector

    messages = conversation_messages(db, conversation_id)
    counts = {"proposed": 0, "created": 0, "confirmed": 0}
    now = datetime.now(timezone.utc)
    if messages:
        title = db.execute(sa_text(
            "SELECT title FROM conversations WHERE id = :cid"),
            {"cid": conversation_id}).scalar() or ""
        prompt, ref_map = build_prompt(title, messages)
        propositions = parse_propositions(llm(prompt))
        counts["proposed"] = len(propositions)
        vectors = embedder([p["statement"] for p in propositions]) if propositions else []
        for proposition, vec in zip(propositions, vectors):
            vec_literal = to_pgvector(vec)
            similar = _find_similar(db, vec_literal)
            if similar and similar[1] >= DEDUP_SIMILARITY:
                db.execute(sa_text(
                    "UPDATE memory_items SET last_confirmed_at = :now, "
                    "authority = CASE WHEN authority = 'observed' THEN 'corroborated' "
                    "ELSE authority END WHERE memory_id = :mid"),
                    {"now": now, "mid": similar[0]})
                counts["confirmed"] += 1
                continue
            memory_id = "mem_" + uuid.uuid4().hex[:16]
            db.execute(sa_text("""
                INSERT INTO memory_items (memory_id, kind, statement, project_id,
                    status, authority, confidence, first_observed_at,
                    last_confirmed_at, sensitivity, extraction_run_id,
                    source_conversation_id, embedding)
                VALUES (:mid, :kind, :stmt, NULL, 'current', 'observed', :conf,
                    :now, :now, 'confidential-personal', :run, :cid,
                    CAST(:vec AS vector))
            """), {"mid": memory_id, "kind": proposition["kind"],
                   "stmt": proposition["statement"], "conf": proposition["confidence"],
                   "now": now, "run": run_id, "cid": conversation_id,
                   "vec": vec_literal})
            for ref in proposition["evidence"]:
                event_id = ref_map.get(ref)
                if event_id:
                    excerpt = next((m["text"][:300] for m in messages
                                    if m["ref"] == ref), None)
                    db.execute(sa_text(
                        "INSERT INTO memory_evidence (id, memory_id, event_id, excerpt) "
                        "VALUES (:id, :mid, :eid, :ex)"),
                        {"id": "evi_" + uuid.uuid4().hex[:16], "mid": memory_id,
                         "eid": event_id, "ex": excerpt})
            counts["created"] += 1
    db.execute(sa_text(
        "UPDATE conversations SET extracted_at = :now WHERE id = :cid"),
        {"now": now, "cid": conversation_id})
    db.commit()
    return counts
