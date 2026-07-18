"""Cloud container entrypoint: build DATABASE_URL from the RDS secret,
apply migrations, then serve the API."""
from __future__ import annotations

import json
import os
from urllib.parse import quote_plus


def database_url_from_secret(secret_json: str, sslmode: str = "require") -> str:
    secret = json.loads(secret_json)
    password = quote_plus(secret["password"])
    host = secret["host"]
    port = secret.get("port", 5432)
    dbname = secret.get("dbname", "pis")
    username = secret["username"]
    return f"postgresql+psycopg://{username}:{password}@{host}:{port}/{dbname}?sslmode={sslmode}"


def alembic_config(url: str):
    """Alembic Config with the URL safely escaped: configparser treats % as
    interpolation syntax, so URL-encoded passwords (e.g. %3D) crash unless
    doubled."""
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def main() -> None:
    secret_json = os.environ.get("PIS_DB_SECRET")
    if secret_json:
        os.environ["PIS_DATABASE_URL"] = database_url_from_secret(
            secret_json, os.environ.get("PIS_DB_SSLMODE", "require")
        )

    from alembic import command

    command.upgrade(alembic_config(os.environ["PIS_DATABASE_URL"]), "head")

    import uvicorn

    from pis.api.app import create_app

    uvicorn.run(create_app(), host="0.0.0.0", port=8800)


if __name__ == "__main__":
    main()
