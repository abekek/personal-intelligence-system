# Threat model — Phases 0-1

Assets: personal conversation history (Claude Code turns), git activity
metadata, the event ledger itself. Trust zones: (1) producing machine
(hooks, daemon), (2) local services (API + Postgres on 127.0.0.1), (3) the
repo on disk. No network exposure beyond loopback in these phases.

| Threat | Vector | Mitigation |
|---|---|---|
| Secret leakage into ledger | Terminal output / prompts contain API keys, tokens, SSNs | Daemon redacts via pis.security before enqueue; ingest rejects any event whose text still matches a detector; rejection audited |
| Employer data capture | Hook fires in an employer repo | PolicyEngine denied_repo/denied_path patterns checked at daemon and again at ingest; rejected + audited |
| Forged capture posts | Any local process posts to daemon/API | Daemon requires X-Capture-Token; API requires Authorization bearer; both bind 127.0.0.1 only |
| Forged GitHub webhooks | Attacker posts fake push payloads | HMAC SHA-256 X-Hub-Signature-256 verification with compare_digest; delivery-id dedup |
| Replay/duplication | Outbox retries, webhook redeliveries, re-run imports | Idempotent ingest: unique event_id + content_hash; webhook_deliveries table; replays return "duplicate" |
| Ledger tampering | Buggy code or SQL UPDATE/DELETE on events | BEFORE UPDATE/DELETE trigger raises; projections rebuildable from ledger |
| Hook blocks/breaks Claude Code | Daemon down, slow network, parse error | Hook is stdlib-only, 2s timeout, catches all exceptions, always exits 0 (subprocess-tested) |
| Prompt injection via captured content | Retrieved text treated as instructions later | Retrieval returns data with provenance only; no tool execution from stored text; write surface limited to ingest |
| Sensitive data at rest | Laptop theft | FileVault assumed for disk; Postgres and object store are local-only; revisit (app-layer encryption) before any remote sync — accepted risk for Phase 0-1 |

Out of scope until their phases: browser extension pairing, OAuth scopes,
remote MCP exposure, finance isolation, export handling.
