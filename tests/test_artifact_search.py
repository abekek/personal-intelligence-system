from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.retrieval.search import search_fts
from tests.conftest import TEST_DB_URL


def test_uploaded_document_is_searchable(engine, db, tmp_path):
    settings = Settings(database_url=TEST_DB_URL, object_store_backend="fs",
                        object_store_dir=tmp_path / "objects")
    with TestClient(create_app(settings)) as client:
        auth = {"Authorization": f"Bearer {settings.ingest_token}"}
        body = ("Recommendation letter strategy for the grant proposal. " * 20).encode()
        r = client.post("/v1/artifacts", params={"filename": "letter-strategy.md"},
                        content=body, headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "created"

        hits = client.get("/v1/search",
                          params={"q": "recommendation letter strategy", "mode": "fts"},
                          headers=auth).json()
        doc_hits = [h for h in hits if h["kind"] == "document"]
        assert doc_hits and "letter-strategy.md" in doc_hits[0]["snippet"]

        again = client.post("/v1/artifacts", params={"filename": "letter-strategy.md"},
                            content=body, headers=auth)
        assert again.json()["status"] == "duplicate"


def test_conversation_document_reference(engine, db, tmp_path):
    from pis import ids
    from pis.ingest.service import ingest_events
    from pis.policy.engine import PolicyEngine
    from pathlib import Path as P
    from tests.test_normalize_chat import note_event

    settings = Settings(database_url=TEST_DB_URL, object_store_backend="fs",
                        object_store_dir=tmp_path / "objects")
    with TestClient(create_app(settings)) as client:
        auth = {"Authorization": f"Bearer {settings.ingest_token}"}
        # a conversation exists
        ingest_events(db, [note_event(text="Discussing the strategy doc",
                                      key="conv-with-doc")],
                      PolicyEngine.load(P("config")))
        conv_id = ids.conversation_id("claude", "conv-with-doc")

        r = client.post("/v1/artifacts",
                        params={"filename": "strategy.md",
                                "conversation_uuid": "conv-with-doc",
                                "provider": "claude"},
                        content=b"The grand strategy document body.", headers=auth)
        assert r.json()["status"] == "created"

        conv = client.get(f"/v1/conversations/{conv_id}", headers=auth).json()
        assert conv["documents"] == [{
            "artifact_id": r.json()["artifact_id"],
            "filename": "strategy.md",
            "resolution_status": "resolved_exact",
        }]

        # duplicate upload from the same conversation adds no second reference
        client.post("/v1/artifacts",
                    params={"filename": "strategy.md",
                            "conversation_uuid": "conv-with-doc"},
                    content=b"The grand strategy document body.", headers=auth)
        conv = client.get(f"/v1/conversations/{conv_id}", headers=auth).json()
        assert len(conv["documents"]) == 1
