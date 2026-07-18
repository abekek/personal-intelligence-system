# ADR 0003: Localhost capture daemon with durable outbox

Status: accepted (2026-07-18)

Producers (Claude Code hooks now, browser extension later) POST to a FastAPI
daemon bound to 127.0.0.1:8787, authenticated with a device token header.
The daemon redacts secrets locally, appends to a SQLite outbox, then flushes
to the ingestion API with retry. Rationale: hooks must never block Claude
Code (fire-and-forget, 2s timeout, always exit 0); capture must survive the
API being down (outbox buffers); secrets should be redacted before leaving
the producing machine. Phase 0 uses env/file tokens; OS-keychain storage and
device registration are deferred to the browser-extension phase.
