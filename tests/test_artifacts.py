import sqlalchemy as sa

from pis.artifacts.extract import chunk_blocks, extract_text, ExtractedBlock
from pis.artifacts.service import ingest_file
from pis.storage.objects import ObjectStore


def test_extract_plain_and_chunking():
    extraction = extract_text(b"hello world " * 10, "notes.md")
    assert extraction.parser == "plain"
    long_block = ExtractedBlock("x" * 4000, {"type": "file"})
    chunks = chunk_blocks([long_block], size=1500, overlap=200)
    assert len(chunks) == 4  # ceil((4000-1500)/1300)+1 with tail
    assert chunks[1].locator["part"] == 1


def test_nul_bytes_sanitized(db, tmp_path):
    from pis.storage.objects import ObjectStore
    payload = ("evidence checklist item\x00with embedded NUL " * 40).encode()
    result = ingest_file(db, ObjectStore(tmp_path), payload, "weird.txt")
    assert result.status == "created" and result.chunks >= 1


def test_extract_unsupported_returns_none():
    assert extract_text(b"\x00\x01binary", "image.png") is None


def test_credential_filenames_denied(db, tmp_path):
    from pis.security.filenames import is_denied_filename
    assert is_denied_filename("ddb-ai-companion_accessKeys.csv")
    assert is_denied_filename(".env.production")
    assert is_denied_filename("server.pem")
    assert not is_denied_filename("expenses.csv")
    assert not is_denied_filename("environment-policy.pdf")
    result = ingest_file(db, ObjectStore(tmp_path), b"AKIA...,secret",
                         "prod_accessKeys.csv")
    assert result.status == "denied" and result.artifact_id is None
    total = db.execute(sa.text("SELECT count(*) FROM artifacts")).scalar()
    assert total == 0


def test_extract_caps_huge_text():
    from pis.artifacts.extract import MAX_TEXT_CHARS
    extraction = extract_text(b"a" * (MAX_TEXT_CHARS * 4), "huge.txt")
    total = sum(len(b.text) for b in extraction.blocks)
    assert total == MAX_TEXT_CHARS
    assert extraction.blocks[-1].locator["truncated"] is True
    # bounded chunk count is the point: a capped file must not fan out
    # into thousands of embedding calls
    assert len(chunk_blocks(extraction.blocks)) < 200


def test_ingest_file_roundtrip_and_dedupe(db, tmp_path):
    store = ObjectStore(tmp_path)
    payload = ("Grant application evidence checklist\n" * 30).encode()

    result = ingest_file(db, store, payload, "checklist.txt",
                         source_meta={"origin": "test"})
    assert result.status == "created" and result.chunks >= 1
    assert store.exists("sha256:" +
                        __import__("hashlib").sha256(payload).hexdigest())

    again = ingest_file(db, store, payload, "checklist.txt")
    assert again.status == "duplicate"

    rows = db.execute(sa.text(
        "SELECT text_content FROM artifact_chunks WHERE version_id = :v"),
        {"v": result.version_id}).fetchall()
    assert rows and "evidence checklist" in rows[0][0]


def test_ingest_unsupported_stores_binary(db, tmp_path):
    store = ObjectStore(tmp_path)
    result = ingest_file(db, store, b"\x89PNG...", "photo.png")
    assert result.status == "unsupported"
    version = db.execute(sa.text(
        "SELECT text_extracted FROM artifact_versions WHERE id = :i"),
        {"i": result.version_id}).scalar()
    assert version is False
