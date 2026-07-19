from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from pis.db.models import Conversation


@dataclass
class SearchHit:
    kind: str  # "message" | "turn" | "commit"
    ref_id: str
    conversation_id: str | None
    snippet: str
    event_id: str | None
    score: float


def _snippet(content: str, q: str, width: int = 80) -> str:
    idx = content.lower().find(q.lower())
    if idx < 0:
        return content[:width]
    start = max(0, idx - width // 2)
    return content[start : idx + len(q) + width // 2]


def search_exact(db: Session, q: str, limit: int = 20) -> list[SearchHit]:
    hits: list[SearchHit] = []
    rows = db.execute(text("""
        SELECT m.conversation_id, r.message_id, r.text_content, r.event_id
        FROM message_revisions r JOIN messages m ON m.id = r.message_id
        WHERE r.text_content ILIKE '%' || :q || '%'
        ORDER BY r.created_at DESC LIMIT :limit
    """), {"q": q, "limit": limit})
    for conv_id, mid, content, event_id in rows:
        hits.append(SearchHit("message", mid, conv_id, _snippet(content, q), event_id, 1.0))

    rows = db.execute(text("""
        SELECT t.conversation_id, t.id, coalesce(t.user_prompt, '') || ' ' ||
               coalesce(t.assistant_response, ''), t.event_id
        FROM turns t
        WHERE :q = ANY(t.changed_files)
        LIMIT :limit
    """), {"q": q, "limit": limit})
    for conv_id, tid, content, event_id in rows:
        hits.append(SearchHit("turn", tid, conv_id, _snippet(content, q), event_id, 1.0))

    rows = db.execute(text("""
        SELECT g.id, coalesce(g.title, ''), g.event_id
        FROM git_objects g
        WHERE g.object_key = :q OR :q = ANY(g.files)
        LIMIT :limit
    """), {"q": q, "limit": limit})
    for gid, title, event_id in rows:
        hits.append(SearchHit("commit", gid, None, title[:120], event_id, 1.0))
    return hits[:limit]


def search_fts(db: Session, q: str, limit: int = 20) -> list[SearchHit]:
    hits: list[SearchHit] = []
    rows = db.execute(text("""
        SELECT m.conversation_id, r.message_id, r.text_content, r.event_id,
               ts_rank(r.tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM message_revisions r JOIN messages m ON m.id = r.message_id
        WHERE r.tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY rank DESC LIMIT :limit
    """), {"q": q, "limit": limit})
    for conv_id, mid, content, event_id, rank in rows:
        hits.append(SearchHit("message", mid, conv_id, content[:160], event_id, float(rank)))

    rows = db.execute(text("""
        SELECT t.conversation_id, t.id, coalesce(t.user_prompt, '') || ' ' ||
               coalesce(t.assistant_response, ''), t.event_id,
               ts_rank(t.tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM turns t
        WHERE t.tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY rank DESC LIMIT :limit
    """), {"q": q, "limit": limit})
    for conv_id, tid, content, event_id, rank in rows:
        hits.append(SearchHit("turn", tid, conv_id, content[:160], event_id, float(rank)))

    rows = db.execute(text("""
        SELECT c.id, a.original_filename, c.text_content,
               ts_rank(c.tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM artifact_chunks c
        JOIN artifact_versions v ON v.id = c.version_id
        JOIN artifacts a ON a.artifact_id = v.artifact_id
        WHERE c.tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY rank DESC LIMIT :limit
    """), {"q": q, "limit": limit})
    for chunk_id, filename, content, rank in rows:
        hits.append(SearchHit("document", chunk_id, None,
                              f"[{filename}] {content[:140]}", None, float(rank)))

    rows = db.execute(text("""
        SELECT memory_id, kind, statement, source_conversation_id,
               ts_rank(tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM memory_items WHERE status = 'current'
          AND tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY rank DESC LIMIT :limit
    """), {"q": q, "limit": limit})
    for memory_id, kind, statement, conv_id, rank in rows:
        hits.append(SearchHit("memory", memory_id, conv_id,
                              f"({kind}) {statement[:150]}", None, float(rank)))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def search_semantic(db: Session, query_vec: str, limit: int = 20) -> list[SearchHit]:
    """query_vec is a pgvector literal ('[..]'). Cosine distance ranking."""
    hits: list[SearchHit] = []
    rows = db.execute(text("""
        SELECT m.conversation_id, r.message_id, r.text_content, r.event_id,
               1 - (r.embedding <=> CAST(:vec AS vector)) AS score
        FROM message_revisions r JOIN messages m ON m.id = r.message_id
        WHERE r.embedding IS NOT NULL
        ORDER BY r.embedding <=> CAST(:vec AS vector) LIMIT :limit
    """), {"vec": query_vec, "limit": limit})
    for conv_id, mid, content, event_id, score in rows:
        hits.append(SearchHit("message", mid, conv_id, (content or "")[:160],
                              event_id, float(score)))
    rows = db.execute(text("""
        SELECT t.conversation_id, t.id,
               coalesce(t.user_prompt, '') || ' ' || coalesce(t.assistant_response, ''),
               t.event_id, 1 - (t.embedding <=> CAST(:vec AS vector)) AS score
        FROM turns t WHERE t.embedding IS NOT NULL
        ORDER BY t.embedding <=> CAST(:vec AS vector) LIMIT :limit
    """), {"vec": query_vec, "limit": limit})
    for conv_id, tid, content, event_id, score in rows:
        hits.append(SearchHit("turn", tid, conv_id, content[:160], event_id, float(score)))
    rows = db.execute(text("""
        SELECT c.id, a.original_filename, c.text_content,
               1 - (c.embedding <=> CAST(:vec AS vector)) AS score
        FROM artifact_chunks c
        JOIN artifact_versions v ON v.id = c.version_id
        JOIN artifacts a ON a.artifact_id = v.artifact_id
        WHERE c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT :limit
    """), {"vec": query_vec, "limit": limit})
    for chunk_id, filename, content, score in rows:
        hits.append(SearchHit("document", chunk_id, None,
                              f"[{filename}] {content[:140]}", None, float(score)))
    rows = db.execute(text("""
        SELECT memory_id, kind, statement, source_conversation_id,
               1 - (embedding <=> CAST(:vec AS vector)) AS score
        FROM memory_items WHERE status = 'current' AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :limit
    """), {"vec": query_vec, "limit": limit})
    for memory_id, kind, statement, conv_id, score in rows:
        hits.append(SearchHit("memory", memory_id, conv_id,
                              f"({kind}) {statement[:150]}", None, float(score)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


# Observer-contamination gate: memories mined from the system's own
# development sessions are excluded from packs unless the topic is about
# the system itself. (The evidence is primary, so authority gating cannot
# catch this — project scoping can.)
META_PROJECTS = ("personal-intelligence-system",)
META_TOPIC_HINTS = ("personal-intelligence", "ledger", "memory system",
                    "knowledge system", "pis", "mcp connector", "context pack")


def _meta_filter_sql(topic: str) -> str:
    if any(hint in topic.lower() for hint in META_TOPIC_HINTS):
        return ""  # topic is about the system — meta memories are fair game
    projects = ", ".join(f"'{p}'" for p in META_PROJECTS)
    return f" AND (project_id IS NULL OR project_id NOT IN ({projects}))"


def build_context_pack(db: Session, topic: str, query_vec: str | None,
                       limit: int = 12) -> dict:
    """Assembled current knowledge about a topic: memories with provenance,
    plus the most relevant raw conversations."""
    hits = search_hybrid(db, topic, query_vec, limit=30)
    # Memories are retrieved directly — they must never lose ranking fights
    # against the raw messages they were distilled from.
    # Centrality gate: a memory must be ABOUT the topic, not merely co-occur
    # with it; higher-sensitivity memories need a closer match to surface.
    meta_filter = _meta_filter_sql(topic)
    memory_ids: list[str] = []
    if query_vec is not None:
        for memory_id, similarity, sensitivity in db.execute(text(f"""
            SELECT memory_id, 1 - (embedding <=> CAST(:vec AS vector)), sensitivity
            FROM memory_items
            WHERE status = 'current' AND authority != 'asserted'
              AND embedding IS NOT NULL{meta_filter}
            ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :limit
        """), {"vec": query_vec, "limit": limit * 2}):
            floor = 0.60 if sensitivity == "highly-sensitive" else 0.45
            if similarity >= floor:
                memory_ids.append(memory_id)
    for (memory_id,) in db.execute(text(f"""
        SELECT memory_id FROM memory_items
        WHERE status = 'current' AND authority != 'asserted'
          AND tsv @@ websearch_to_tsquery('english', :q){meta_filter}
        ORDER BY ts_rank(tsv, websearch_to_tsquery('english', :q)) DESC
        LIMIT :limit
    """), {"q": topic, "limit": limit}):
        if memory_id not in memory_ids:
            memory_ids.append(memory_id)
    memory_ids = memory_ids[:limit]
    memories = []
    for memory_id in memory_ids:
        row = db.execute(text("""
            SELECT kind, statement, confidence, authority, last_confirmed_at
            FROM memory_items WHERE memory_id = :m"""), {"m": memory_id}).first()
        if row is None:
            continue
        evidence = [event_id for (event_id,) in db.execute(text(
            "SELECT event_id FROM memory_evidence WHERE memory_id = :m"),
            {"m": memory_id})]
        memories.append({
            "memory_id": memory_id, "kind": row[0], "statement": row[1],
            "confidence": row[2], "authority": row[3],
            "last_confirmed_at": str(row[4]), "evidence_event_ids": evidence,
        })
    conversations: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.kind in ("message", "turn") and hit.conversation_id not in seen:
            seen.add(hit.conversation_id)
            title = db.execute(text(
                "SELECT title FROM conversations WHERE id = :c"),
                {"c": hit.conversation_id}).scalar()
            conversations.append({"conversation_id": hit.conversation_id,
                                  "title": title, "snippet": hit.snippet})
        if len(conversations) >= 5:
            break
    return {"topic": topic, "memories": memories,
            "related_conversations": conversations}


def rrf_fuse(result_lists: list[list[SearchHit]], limit: int = 20, k: int = 60) -> list[SearchHit]:
    scores: dict[str, float] = {}
    best: dict[str, SearchHit] = {}
    for results in result_lists:
        for rank, hit in enumerate(results):
            key = f"{hit.kind}:{hit.ref_id}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in best:
                best[key] = hit
    fused = sorted(best.items(), key=lambda kv: scores[kv[0]], reverse=True)
    out = []
    for key, hit in fused[:limit]:
        out.append(SearchHit(hit.kind, hit.ref_id, hit.conversation_id,
                             hit.snippet, hit.event_id, round(scores[key], 6)))
    return out


def search_hybrid(db: Session, q: str, query_vec: str | None, limit: int = 20) -> list[SearchHit]:
    lists = [search_fts(db, q, limit=limit)]
    if query_vec is not None:
        lists.append(search_semantic(db, query_vec, limit=limit))
    return rrf_fuse(lists, limit=limit)


def get_conversation(db: Session, conversation_id: str) -> dict | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    messages = [
        {"id": mid, "role": role, "position": pos, "text": content,
         "revision": rev, "event_id": event_id}
        for mid, role, pos, content, rev, event_id in db.execute(text("""
            SELECT m.id, m.role, m.position, r.text_content, r.revision, r.event_id
            FROM messages m
            JOIN message_revisions r ON r.message_id = m.id
            AND r.revision = (SELECT max(revision) FROM message_revisions WHERE message_id = m.id)
            WHERE m.conversation_id = :cid
            ORDER BY m.position
        """), {"cid": conversation_id})
    ]
    tool_events = [
        {"id": tid, "tool_name": name, "summary": summary, "event_id": event_id}
        for tid, name, summary, event_id in db.execute(text("""
            SELECT id, tool_name, summary, event_id FROM tool_events
            WHERE conversation_id = :cid ORDER BY occurred_at, id
        """), {"cid": conversation_id})
    ]
    documents = [
        {"artifact_id": aid, "filename": name, "resolution_status": status}
        for aid, name, status in db.execute(text("""
            SELECT artifact_id, display_name, resolution_status
            FROM artifact_references WHERE conversation_id = :cid ORDER BY created_at
        """), {"cid": conversation_id})
    ]
    return {
        "conversation": {
            "id": conv.id, "provider": conv.provider, "title": conv.title,
            "provider_conversation_id": conv.provider_conversation_id,
            "sensitivity": conv.sensitivity,
        },
        "messages": messages,
        "tool_events": tool_events,
        "documents": documents,
    }
