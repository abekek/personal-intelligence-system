import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from pis.github.webhook import handle_delivery, verify_signature
from pis.mcp_server.app import BearerGate, build_mcp
from pis.oauth.router import build_router as build_oauth_router
from pis.retrieval.search import (
    get_conversation,
    search_exact,
    search_fts,
    search_hybrid,
)

import pis.normalize.chat  # noqa: F401  (registers conversation.message.completed)
import pis.normalize.claude_code  # noqa: F401  (registers claude_code.turn.completed)
from pis.config import Settings
from pis.db.engine import get_engine, make_session_factory
from pis.ingest.service import IngestResult, ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent


class EventBatch(BaseModel):
    events: list[CanonicalEvent]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = get_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    policy = PolicyEngine.load(settings.config_dir)

    mcp = build_mcp(settings, session_factory, policy)
    mcp_asgi = mcp.streamable_http_app()

    # Mounted sub-app lifespans don't run automatically; drive the MCP
    # session manager from the outer app's lifespan.
    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="pis-api", lifespan=lifespan)
    app.state.settings = settings

    def db_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def require_token(authorization: str = Header(default="")) -> None:
        if authorization != f"Bearer {settings.ingest_token}":
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/v1/events", dependencies=[Depends(require_token)])
    def post_events(
        batch: EventBatch, db: Session = Depends(db_session)
    ) -> list[IngestResult]:
        return ingest_events(db, batch.events, policy)

    def _query_vec(q: str) -> str | None:
        if not settings.embeddings_enabled:
            return None
        try:
            from pis.embeddings import embed_texts, to_pgvector
            return to_pgvector(embed_texts([q], settings)[0])
        except Exception:
            return None  # degrade to FTS-only

    @app.get("/v1/search", dependencies=[Depends(require_token)])
    def search(q: str, mode: str = "hybrid", db: Session = Depends(db_session)):
        if mode == "exact":
            return [asdict(h) for h in search_exact(db, q)]
        if mode == "fts":
            return [asdict(h) for h in search_fts(db, q)]
        return [asdict(h) for h in search_hybrid(db, q, _query_vec(q))]

    @app.post("/v1/admin/embed-backfill", dependencies=[Depends(require_token)])
    def embed_backfill(limit: int = 100, db: Session = Depends(db_session)):
        from pis.embeddings import embed_texts, to_pgvector
        rows = list(db.execute(sa_text("""
            SELECT 'revision' AS kind, id, coalesce(text_content, '') AS content
            FROM message_revisions WHERE embedding IS NULL
            UNION ALL
            SELECT 'turn', id,
                   coalesce(user_prompt, '') || ' ' || coalesce(assistant_response, '')
            FROM turns WHERE embedding IS NULL
            LIMIT :limit
        """), {"limit": limit}))
        todo = [(k, r, c) for k, r, c in rows if c.strip()]
        if todo:
            vectors = embed_texts([c for _, _, c in todo], settings)
            for (kind, rid, _), vec in zip(todo, vectors):
                table = "message_revisions" if kind == "revision" else "turns"
                db.execute(sa_text(
                    f"UPDATE {table} SET embedding = CAST(:v AS vector) WHERE id = :r"
                ), {"v": to_pgvector(vec), "r": rid})
        # rows with empty content are skipped forever unless marked; stamp them
        for kind, rid, content in rows:
            if not content.strip():
                table = "message_revisions" if kind == "revision" else "turns"
                db.execute(sa_text(
                    f"UPDATE {table} SET embedding = CAST(:v AS vector) WHERE id = :r"
                ), {"v": "[" + ",".join(["0"] * 1024) + "]", "r": rid})
        db.commit()
        return {"embedded": len(todo), "scanned": len(rows)}

    @app.get("/v1/conversations/{conversation_id}", dependencies=[Depends(require_token)])
    def conversation(conversation_id: str, db: Session = Depends(db_session)):
        result = get_conversation(db, conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return result

    @app.post("/v1/github/webhook")
    async def github_webhook(request: Request, db: Session = Depends(db_session)):
        body = await request.body()
        if not verify_signature(settings.github_webhook_secret, body,
                                request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=401, detail="bad signature")
        result = handle_delivery(
            db, policy,
            request.headers.get("X-GitHub-Delivery", ""),
            request.headers.get("X-GitHub-Event", ""),
            json.loads(body),
        )
        return {"status": result.status, "created_commits": result.created_commits}

    @app.get("/v1/git/commits/{sha}/sessions", dependencies=[Depends(require_token)])
    def commit_sessions(sha: str, db: Session = Depends(db_session)):
        rows = db.execute(sa_text("""
            SELECT l.session_id, l.confidence, l.signals, g.object_key
            FROM session_commit_links l
            JOIN git_objects g ON g.id = l.git_object_id
            WHERE g.object_key = :sha
        """), {"sha": sha})
        return [{"session_id": sid, "confidence": conf, "signals": signals, "commit": key}
                for sid, conf, signals, key in rows]

    @app.post("/v1/artifacts", dependencies=[Depends(require_token)])
    async def upload_artifact(request: Request, filename: str,
                              conversation_uuid: str = "",
                              provider: str = "claude",
                              db: Session = Depends(db_session)):
        from pis import ids as pis_ids
        from pis.artifacts.service import ingest_file, link_artifact_to_conversation
        from pis.storage.s3 import make_object_store
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")
        embedder = None
        if settings.embeddings_enabled:
            from pis.embeddings import embed_texts
            embedder = lambda texts: embed_texts(texts, settings)  # noqa: E731
        result = ingest_file(db, make_object_store(settings), data, filename,
                             source_meta={"via": "api"}, embedder=embedder)
        if conversation_uuid and result.artifact_id:
            link_artifact_to_conversation(
                db, result.artifact_id,
                pis_ids.conversation_id(provider, conversation_uuid),
                filename, {"provider": provider, "conversation_uuid": conversation_uuid},
            )
        return {"status": result.status, "artifact_id": result.artifact_id,
                "version_id": result.version_id, "chunks": result.chunks}

    app.include_router(build_oauth_router(settings, db_session))

    public = settings.public_url.rstrip("/")

    @app.get("/.well-known/oauth-protected-resource/mcp")
    @app.get("/.well-known/oauth-protected-resource")
    def protected_resource_metadata():
        return {
            "resource": f"{public}/mcp",
            "authorization_servers": [public],
            "scopes_supported": ["kb"],
            "bearer_methods_supported": ["header"],
        }

    app.mount("/mcp", BearerGate(mcp_asgi, settings, session_factory))
    # Kill the router's /mcp -> /mcp/ trailing-slash 307: MCP clients drop
    # Authorization on redirects, so exact /mcp must route directly.
    app.add_middleware(McpSlashRewrite)

    return app


class McpSlashRewrite:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            scope["raw_path"] = b"/mcp/"
        await self.app(scope, receive, send)
