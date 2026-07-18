# ADR 0005: Server-side project and sensitivity policy

Status: accepted (2026-07-18)

Policy lives in config/*.yaml (projects, sensitivity default, denied path and
repo patterns) loaded into a PolicyEngine used by the ingestion service and
webhook handler — enforcement is in code paths that write, never in prompts.
Denied employer repos/paths are rejected at ingest with an audit record;
content matching secret detectors is rejected server-side even though the
daemon also redacts client-side (defense in depth). Every event carries a
sensitivity label; highly-sensitive zones (NIW, finance) get separate scopes
and schemas in later phases and are absent from Phases 0-1 by design.
