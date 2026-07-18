from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIS_")

    database_url: str = "postgresql+psycopg://pis:pis@127.0.0.1:5433/pis"
    config_dir: Path = Path("config")
    object_store_dir: Path = Path("var/objects")
    object_store_backend: str = "fs"
    s3_bucket: str = ""
    ingest_token: str = "dev-token"
    daemon_token: str = "dev-daemon-token"
    daemon_outbox_path: Path = Path("var/outbox.sqlite3")
    api_url: str = "http://127.0.0.1:8800"
    github_webhook_secret: str = "dev-webhook-secret"
    public_url: str = "http://127.0.0.1:8800"
    oauth_passcode: str = "dev-passcode"
    embeddings_enabled: bool = False
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    bedrock_region: str = "us-east-1"
