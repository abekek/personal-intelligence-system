from pis.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.ingest_token


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("PIS_INGEST_TOKEN", "tok-123")
    assert Settings().ingest_token == "tok-123"
