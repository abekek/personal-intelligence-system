# personal-intelligence-system

Conversation-first personal intelligence system: an immutable event ledger that
captures Claude Code sessions and GitHub activity, derives conversations/turns,
and exposes exact + full-text retrieval. Spec: conversation_first v3.

## Quick start

    docker compose up -d postgres
    uv sync
    uv run alembic upgrade head
    uv run pytest -q

## Services

    uv run python -m pis.api      # ingestion + retrieval API on 127.0.0.1:8800
    uv run python -m pis.daemon   # local capture daemon on 127.0.0.1:8787

## Claude Code hook

See `integrations/claude-code/settings.example.json`.

## Verification (Phase 0-1 exit criteria)

- `uv run pytest -q` — full suite incl. `tests/test_acceptance_slice1.py`,
  which walks spec §26: hook -> daemon -> ledger -> normalizer -> search ->
  evidence, replays every input (no duplicates), then a signed GitHub push
  that links back to the producing session.
- Ledger immutability: `tests/test_db.py` (UPDATE/DELETE raise).
- Policy: `tests/test_ingest.py`, `tests/test_github_webhook.py`
  (denied repo/path + secret content rejected and audited).

## Deployment (AWS core, local capture)

The core runs on AWS (us-east-1; set your account/profile via env),
provisioned by CDK in `infra/` (stack `PisCore`): RDS Postgres 16
(db.t4g.micro, encrypted, 7-day backups, private in the default VPC), App
Runner service (image built from the repo Dockerfile; migrations run at boot
via `pis.serve`), S3 object store, Secrets Manager tokens
(`pis/ingest-token`, `pis/webhook-secret`). Capture stays on this Mac; the
daemon's SQLite outbox buffers while offline.

    cd infra && AWS_PROFILE=abekek npx cdk deploy   # infra changes
    ./integrations/claude-code/install-daemon.sh    # launchd daemon -> cloud API

## Live wiring (user setup)

1. Run `integrations/claude-code/install-daemon.sh` (writes ~/.pis/daemon.env
   with the cloud URL + tokens, loads the launchd daemon).
2. Enable the Stop hook by copying `integrations/claude-code/settings.example.json`
   contents into `~/.claude/settings.json` (global) or a repo's
   `.claude/settings.json` (per-project). Before going global, replace the
   placeholder employer patterns in `config/denied_paths.yaml`.
3. GitHub webhooks: point a repo/App webhook at
   `POST <ServiceUrl>/v1/github/webhook` with the `pis/webhook-secret` value.

## Connect claude.ai (web + mobile)

1. Get your passcode:
   `/usr/local/bin/aws secretsmanager get-secret-value --secret-id pis/oauth-passcode --query SecretString --output text --profile abekek --region us-east-1`
2. In claude.ai: Settings → Connectors → Add custom connector → URL:
   `https://<service-url>/mcp` (no client id/secret needed — DCR).
3. Approve the OAuth screen with the passcode. Tools available in chats:
   `kb_search`, `kb_get_conversation`, `kb_get_session_for_commit`,
   `kb_recent_activity`, and `kb_capture_note` (tell Claude "log this to my
   ledger" while chatting — that's the claude.ai capture path; the browser
   extension phase was dropped in favor of it).

Verify a deployment end-to-end:
`uv run python scripts/verify_mcp_live.py https://<service-url> <passcode>`

## Historical backfill

    uv run pis import inspect ~/.claude/projects
    uv run pis import run ~/.claude/projects --mode bootstrap \
      --api-url <ServiceUrl> --token <pis/ingest-token>

Manifests land in `var/import-manifests/`. Reimports are idempotent.
