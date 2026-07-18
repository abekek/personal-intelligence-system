# personal-intelligence-system

Event-sourcing monorepo. `events` table is the immutable source of truth
(DB trigger blocks UPDATE/DELETE); everything else is a rebuildable projection.

## Commands
- `docker compose up -d postgres` — Postgres 16 on 127.0.0.1:5433 (dbs: pis, pis_test)
- `uv run pytest -q` — full test suite (needs Postgres up)
- `uv run alembic upgrade head` — apply migrations

## Layout
- `src/pis/schemas/` canonical event models (Pydantic v2)
- `src/pis/policy/` deny-lists + sensitivity (config/*.yaml)
- `src/pis/security/` secret scanning
- `src/pis/storage/` content-addressed object store
- `src/pis/db/` SQLAlchemy models + engine
- `src/pis/ingest/` idempotent ingestion service + audit
- `src/pis/normalize/` event -> conversation/turn projections
- `src/pis/retrieval/` exact + FTS search
- `src/pis/github/` webhook verification + push handling
- `src/pis/linking/` session-to-commit linking
- `src/pis/api/`, `src/pis/daemon/` FastAPI apps
- `integrations/claude-code/hooks/` stdlib-only hook scripts (python3.11-safe)

## Rules
- Never UPDATE/DELETE ledger rows; new facts are new events/revisions.
- All ingestion idempotent; dedup by event_id + content_hash.
- Policy enforcement lives in code (ingest/github handlers), not prompts.
- No embeddings/pgvector/finance/browser-extension code in Phases 0-1.
- Synthetic fixtures only in tests.
