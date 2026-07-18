import hashlib

from pis.storage.objects import ObjectStore


def test_put_get_roundtrip(tmp_path):
    store = ObjectStore(tmp_path)
    object_id = store.put(b"hello world")
    assert object_id == "sha256:" + hashlib.sha256(b"hello world").hexdigest()
    assert store.get(object_id) == b"hello world"
    assert store.exists(object_id)


def test_put_is_idempotent_and_sharded(tmp_path):
    store = ObjectStore(tmp_path)
    a = store.put(b"data")
    b = store.put(b"data")
    assert a == b
    digest = a.split(":", 1)[1]
    assert store.path_for(a) == tmp_path / "sha256" / digest[:2] / digest[2:4] / digest
    assert store.path_for(a).exists()


def test_missing_object(tmp_path):
    assert not ObjectStore(tmp_path).exists("sha256:" + "0" * 64)
