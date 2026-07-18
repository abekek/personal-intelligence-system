# Dependencies (Phase 0-1) — spec §25.25

Runtime: fastapi (ingestion/retrieval/daemon HTTP apps), pydantic v2
(canonical event schemas + validation at the ledger boundary),
pydantic-settings (12-factor config, PIS_* env), sqlalchemy 2 (typed ORM for
ledger + projections), alembic (migrations), psycopg[binary] (Postgres
driver), httpx (daemon->API forwarding; also FastAPI TestClient transport),
pyyaml (policy config files), structlog (structured logs), uvicorn (serving).

Dev: pytest.

Deliberately absent in these phases: pgvector/embedding clients (exact+FTS
must be evaluated first), any queue/broker (synchronous processing at
single-user scale; jobs table later), browser-extension tooling, GitHub API
client (webhook payloads carry all Phase-1 data; App/API fetch added when
needed), MCP SDK (Phase 5), finance/Plaid (Phase 7).

The Claude Code hook script uses stdlib only (json, urllib, subprocess) so it
runs under system python3 with zero installs and cannot break on venv drift.
