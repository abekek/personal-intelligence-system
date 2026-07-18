from __future__ import annotations

import hashlib
from pathlib import Path


class ObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def put(self, data: bytes) -> str:
        object_id = "sha256:" + hashlib.sha256(data).hexdigest()
        path = self.path_for(object_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return object_id

    def get(self, object_id: str) -> bytes:
        return self.path_for(object_id).read_bytes()

    def exists(self, object_id: str) -> bool:
        return self.path_for(object_id).exists()

    def path_for(self, object_id: str) -> Path:
        digest = object_id.split(":", 1)[1]
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest
