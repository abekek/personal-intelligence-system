"""MCP server: kb read tools + the claude.ai capture write tool.

Tools wrap pis.retrieval / pis.ingest 1:1 (ADR 0006). The streamable-HTTP
app is mounted at /mcp behind BearerGate, which validates our OAuth access
tokens and advertises RFC 9728 resource metadata on 401.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from pis.config import Settings
from pis.ingest.service import ingest_events
from pis.oauth.service import verify_access
from pis.policy.engine import PolicyEngine
from pis.retrieval.search import get_conversation, search_exact, search_fts, search_hybrid
from pis.schemas.events import CanonicalEvent, ContentPart, EventType


def build_mcp(settings: Settings, session_factory, policy: PolicyEngine) -> FastMCP:
    mcp = FastMCP(
        "pis-knowledge",
        instructions=(
            "Personal knowledge ledger: search past Claude Code sessions and "
            "captured notes, fetch conversations, and log important notes/"
            "decisions from this chat with kb_capture_note."
        ),
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                urlparse(settings.public_url).netloc,
                "127.0.0.1:8800", "localhost:8800", "testserver",
            ],
            allowed_origins=["*"],
        ),
    )

    def _query_vec(query: str) -> str | None:
        if not settings.embeddings_enabled:
            return None
        try:
            from pis.embeddings import embed_texts, to_pgvector
            return to_pgvector(embed_texts([query], settings)[0])
        except Exception:
            return None

    @mcp.tool()
    def kb_search(query: str, mode: str = "hybrid", limit: int = 10) -> list[dict]:
        """Search the knowledge ledger (past coding sessions, chats, notes).
        mode: "hybrid" (semantic + keyword, default), "fts" keyword-only,
        "exact" for identifiers/filenames/commit SHAs."""
        with session_factory() as db:
            if mode == "exact":
                return [asdict(h) for h in search_exact(db, query, limit=limit)]
            if mode == "fts":
                return [asdict(h) for h in search_fts(db, query, limit=limit)]
            return [asdict(h) for h in
                    search_hybrid(db, query, _query_vec(query), limit=limit)]

    @mcp.tool()
    def kb_get_conversation(conversation_id: str) -> dict:
        """Fetch a full conversation (messages + tool activity) by conversation id
        (conv_...), as returned in kb_search results."""
        with session_factory() as db:
            result = get_conversation(db, conversation_id)
            return result if result is not None else {"error": "not found"}

    @mcp.tool()
    def kb_get_session_for_commit(sha: str) -> list[dict]:
        """Find the Claude Code session(s) that produced a git commit SHA."""
        from sqlalchemy import text as sa_text
        with session_factory() as db:
            rows = db.execute(sa_text("""
                SELECT l.session_id, l.confidence, l.signals
                FROM session_commit_links l
                JOIN git_objects g ON g.id = l.git_object_id
                WHERE g.object_key = :sha
            """), {"sha": sha})
            return [{"session_id": sid, "confidence": conf, "signals": signals}
                    for sid, conf, signals in rows]

    @mcp.tool()
    def kb_recent_activity(limit: int = 10) -> list[dict]:
        """List the most recently updated conversations in the ledger."""
        from sqlalchemy import text as sa_text
        with session_factory() as db:
            rows = db.execute(sa_text("""
                SELECT id, provider, title, updated_at FROM conversations
                ORDER BY updated_at DESC LIMIT :limit
            """), {"limit": limit})
            return [{"conversation_id": cid, "provider": provider, "title": title,
                     "updated_at": str(updated)}
                    for cid, provider, title, updated in rows]

    @mcp.tool()
    def kb_capture_document(filename: str, content: str, note: str = "") -> dict:
        """Save a document from this chat into the permanent ledger — use when
        the user uploads a file worth keeping or says "save this document".
        Pass the document's full text content and its original filename. The
        document becomes permanently searchable."""
        from pis.artifacts.service import ingest_file
        from pis.storage.s3 import make_object_store
        embedder = None
        if settings.embeddings_enabled:
            from pis.embeddings import embed_texts
            embedder = lambda texts: embed_texts(texts, settings)  # noqa: E731
        with session_factory() as db:
            result = ingest_file(
                db, make_object_store(settings), content.encode("utf-8"), filename,
                source_meta={"origin": "claude.ai", "note": note}, embedder=embedder,
            )
        return {"status": result.status, "artifact_id": result.artifact_id,
                "chunks": result.chunks}

    @mcp.tool()
    def kb_capture_note(
        note: str, conversation_key: str | None = None, role: str = "assistant",
    ) -> dict:
        """Write a note into the permanent knowledge ledger. Use this to log
        decisions, findings, preferences, or anything from this chat worth
        remembering across sessions. conversation_key groups related notes
        (e.g. a topic slug); defaults to a shared notes stream."""
        event = CanonicalEvent(
            event_type=EventType.MESSAGE_COMPLETED,
            provider="claude",
            provider_conversation_id=conversation_key or "claude-ai/notes",
            role=role if role in ("user", "assistant") else "assistant",
            occurred_at=datetime.now(timezone.utc),
            capture_method="manual",
            content_parts=[ContentPart(type="text", text=note)],
            metadata={"source": "mcp_capture"},
        )
        with session_factory() as db:
            [result] = ingest_events(db, [event], policy)
        from pis import ids
        return {
            "status": result.status,
            "reason": result.reason,
            "conversation_id": ids.conversation_id(
                "claude", conversation_key or "claude-ai/notes"),
            "event_id": event.event_id,
        }

    return mcp


class BearerGate:
    """ASGI wrapper: validates our OAuth access tokens before the MCP app;
    401s carry the RFC 9728 resource-metadata pointer for discovery."""

    def __init__(self, inner, settings: Settings, session_factory) -> None:
        self.inner = inner
        self.settings = settings
        self.session_factory = session_factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        info = None
        if auth.startswith("Bearer "):
            with self.session_factory() as db:
                info = verify_access(db, auth.removeprefix("Bearer "))
        if info is None:
            metadata_url = (self.settings.public_url.rstrip("/")
                            + "/.well-known/oauth-protected-resource/mcp")
            await send({
                "type": "http.response.start", "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate",
                     f'Bearer resource_metadata="{metadata_url}"'.encode()),
                ],
            })
            await send({"type": "http.response.body",
                        "body": b'{"error": "unauthorized"}'})
            return
        await self.inner(scope, receive, send)
