# ADR 0004: PostgreSQL only — FTS now, no vector DB, sync SQLAlchemy

Status: accepted (2026-07-18)

One PostgreSQL 16 instance holds the ledger, projections, and search indexes
(generated tsvector columns + GIN). Exact search (identifiers, filenames,
commit SHAs) and websearch_to_tsquery FTS must work and be evaluated before
any embeddings are added (spec §25.18); pgvector is not installed in Phases
0-1. No Kafka/Temporal/queue: processing is synchronous in-request at MVP
scale (single user); a Postgres-backed jobs table is the planned upgrade
path. Sync SQLAlchemy 2 keeps the stack simple; FastAPI threadpools suffice.
The object store is content-addressed local filesystem (objects/sha256/..);
at-rest encryption is delegated to FileVault for now and revisited before
any remote sync (see threat model).
Migration policy: the initial Alembic revision creates the schema from
models via metadata; subsequent revisions use autogenerate diffs.
