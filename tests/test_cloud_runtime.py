import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from pis.api.app import create_app
from pis.config import Settings
from pis.serve import database_url_from_secret
from pis.storage.s3 import S3ObjectStore, make_object_store
from pis.storage.objects import ObjectStore


def test_healthz_needs_no_auth(engine, db):
    settings = Settings(database_url="postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test")
    client = TestClient(create_app(settings))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@mock_aws
def test_s3_object_store_roundtrip():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="pis-test-bucket")
    store = S3ObjectStore("pis-test-bucket")
    object_id = store.put(b"hello cloud")
    assert object_id.startswith("sha256:")
    assert store.exists(object_id)
    assert store.get(object_id) == b"hello cloud"
    assert not store.exists("sha256:" + "0" * 64)
    assert store.put(b"hello cloud") == object_id  # idempotent


def test_make_object_store_picks_backend(tmp_path):
    fs = make_object_store(Settings(object_store_backend="fs", object_store_dir=tmp_path))
    assert isinstance(fs, ObjectStore)
    with mock_aws():
        s3 = make_object_store(Settings(object_store_backend="s3", s3_bucket="b"))
        assert isinstance(s3, S3ObjectStore)
    with pytest.raises(ValueError):
        make_object_store(Settings(object_store_backend="s3", s3_bucket=""))


def test_alembic_config_survives_percent_in_url():
    from pis.serve import alembic_config

    url = "postgresql+psycopg://pis:%3DZ6abc@host:5432/pis?sslmode=require"
    cfg = alembic_config(url)
    # configparser unescapes %% back to % on read
    assert cfg.get_main_option("sqlalchemy.url") == url


def test_database_url_from_secret():
    secret = json.dumps({"username": "pis", "password": "p@ss:w/rd",
                         "host": "db.example.rds.amazonaws.com", "port": 5432,
                         "dbname": "pis"})
    url = database_url_from_secret(secret, sslmode="require")
    assert url.startswith("postgresql+psycopg://pis:")
    assert "p%40ss%3Aw%2Frd" in url
    assert url.endswith("@db.example.rds.amazonaws.com:5432/pis?sslmode=require")
