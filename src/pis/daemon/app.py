from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException

from pis.config import Settings
from pis.daemon.outbox import Outbox
from pis.security.secrets import redact_text


def create_daemon_app(
    settings: Settings | None = None,
    post_fn: Callable[[dict], bool] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    outbox = Outbox(settings.daemon_outbox_path)

    if post_fn is None:
        def post_fn(body: dict) -> bool:
            try:
                response = httpx.post(
                    f"{settings.api_url}/v1/events", json=body,
                    headers={"Authorization": f"Bearer {settings.ingest_token}"},
                    timeout=5.0,
                )
                return response.status_code == 200
            except httpx.HTTPError:
                return False

    app = FastAPI(title="pis-daemon")
    app.state.outbox = outbox

    def require_token(x_capture_token: str = Header(default="")) -> None:
        if x_capture_token != settings.daemon_token:
            raise HTTPException(status_code=401, detail="invalid capture token")

    @app.post("/v1/capture", dependencies=[Depends(require_token)])
    def capture(batch: dict, background: BackgroundTasks):
        for event in batch.get("events", []):
            for part in event.get("content_parts", []):
                if part.get("text"):
                    part["text"] = redact_text(part["text"])
        item_id = outbox.enqueue(batch)
        background.add_task(outbox.flush, post_fn)
        return {"queued": item_id}

    @app.post("/v1/flush", dependencies=[Depends(require_token)])
    def flush():
        return {"sent": outbox.flush(post_fn)}

    @app.get("/v1/health")
    def health():
        return {"pending": len(outbox.pending())}

    return app
