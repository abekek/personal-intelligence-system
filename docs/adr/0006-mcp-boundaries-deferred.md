# ADR 0006: MCP access layer deferred; HTTP API is internal-only

Status: accepted (2026-07-18)

Phases 0-1 expose a bearer-token HTTP API on 127.0.0.1 for ingestion and
retrieval (search, get-conversation, commit-to-session). The remote MCP
server (kb_* tools, OAuth scopes, read-only default) is Phase 5 and will sit
in front of the same retrieval functions (pis.retrieval) — tools map 1:1 to
functions, so no retrieval logic will live in the MCP layer. Until security
and retrieval evaluations pass, nothing is exposed off-machine.
