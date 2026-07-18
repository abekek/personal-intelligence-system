from __future__ import annotations

import hashlib

import boto3
from botocore.exceptions import ClientError

from pis.config import Settings
from pis.storage.objects import ObjectStore


class S3ObjectStore:
    def __init__(self, bucket: str, client=None) -> None:
        self.bucket = bucket
        self.client = client or boto3.client("s3")

    def _key(self, object_id: str) -> str:
        digest = object_id.split(":", 1)[1]
        return f"objects/sha256/{digest[:2]}/{digest[2:4]}/{digest}"

    def put(self, data: bytes) -> str:
        object_id = "sha256:" + hashlib.sha256(data).hexdigest()
        if not self.exists(object_id):
            self.client.put_object(Bucket=self.bucket, Key=self._key(object_id), Body=data)
        return object_id

    def get(self, object_id: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(object_id))
        return response["Body"].read()

    def exists(self, object_id: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(object_id))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def make_object_store(settings: Settings):
    if settings.object_store_backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("PIS_S3_BUCKET required when object_store_backend=s3")
        return S3ObjectStore(settings.s3_bucket)
    return ObjectStore(settings.object_store_dir)
