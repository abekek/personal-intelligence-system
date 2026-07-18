# ADR 0002: Messages are logical; revisions are immutable

Status: accepted (2026-07-18)

A `messages` row is a stable logical identity (deterministic id from
conversation + role + turn). Content lives in `message_revisions`
(message_id, revision N, text, content_parts, source event_id). An edited or
regenerated message appends revision N+1; nothing is overwritten. Retrieval
defaults to the latest revision but historical revisions stay queryable, and
every revision carries the event that produced it (provenance).
