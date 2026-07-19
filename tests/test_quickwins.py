import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.db.engine import get_engine, make_session_factory
from pis.extraction.runner import run_extraction
from tests.conftest import TEST_DB_URL
from tests.test_extraction import embedder, make_llm, seed_transcript, PROP


def test_run_extraction_shared_runner(engine, db):
    seed_transcript(db)
    settings = Settings(database_url=TEST_DB_URL)
    session_factory = make_session_factory(engine)
    result = run_extraction(session_factory, settings, limit=5,
                            llm=make_llm([PROP]), embedder=embedder)
    assert result["processed"] >= 1 and result["created"] == 1
    again = run_extraction(session_factory, settings, limit=5,
                           llm=make_llm([PROP]), embedder=embedder)
    assert again["processed"] == 0 and again["remaining"] == 0


def test_context_pack_endpoint(engine, db):
    seed_transcript(db)
    settings = Settings(database_url=TEST_DB_URL)
    session_factory = make_session_factory(engine)
    run_extraction(session_factory, settings, limit=5,
                   llm=make_llm([PROP]), embedder=embedder)
    with TestClient(create_app(settings)) as client:
        r = client.get("/v1/context-pack", params={"topic": "gp3 STORAGE volumes"},
                       headers={"Authorization": f"Bearer {settings.ingest_token}"})
        assert r.status_code == 200
        assert r.json()["memories"]


def test_session_start_hook_formatting():
    spec = importlib.util.spec_from_file_location(
        "session_start_hook",
        "integrations/claude-code/hooks/session_start_hook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pack = {"memories": [
        {"kind": "decision", "statement": "Chose X over Y for the API"},
        {"kind": "task", "statement": "Finish the migration tests"},
    ]}
    text = module.format_pack("my-repo", pack)
    assert "[Personal Ledger]" in text and "my-repo" in text
    assert "(decision) Chose X" in text and "(task) Finish" in text
    assert module.format_pack("r", {"memories": []}) == ""
