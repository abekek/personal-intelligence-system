# ADR 0001: Event-sourcing ledger as source of truth

Status: accepted (2026-07-18)

Every provider update (Claude Code turn, GitHub push, future chat capture)
becomes one immutable row in `events` with a full JSON payload. All other
tables (conversations, messages, turns, git_objects) are projections derived
from events and can be truncated and rebuilt by replaying the ledger.

Consequences: reprocessing with better extractors is always possible; audits
can show what was known when; dedup happens once at the ledger boundary
(unique event_id + unique content_hash); corrections are new events, never
edits. A Postgres trigger raises on UPDATE/DELETE of `events` so immutability
is enforced by the database, not convention (test-only TRUNCATE is allowed
because row triggers do not fire on TRUNCATE).
