"""Text extraction with structural locators (spec §30.15)."""
from __future__ import annotations

import io
from dataclasses import dataclass

TEXT_SUFFIXES = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".py", ".ts", ".js", ".tsx", ".sh", ".toml", ".tex", ".html", ".ini",
}


def _clean(text: str) -> str:
    # Postgres text columns reject NUL bytes; some PDF extractions contain them
    return text.replace("\x00", "")


@dataclass
class ExtractedBlock:
    text: str
    locator: dict

    def __post_init__(self):
        self.text = _clean(self.text)


@dataclass
class Extraction:
    parser: str
    blocks: list[ExtractedBlock]


# Every extracted char becomes chunk rows + one embedding call per ~1.3KB;
# an uncapped 16MB text file means ~12k synchronous Bedrock calls in one
# request, which starves health checks and gets the instance replaced
# mid-request. Cap keeps the worst case at ~150 chunks.
MAX_TEXT_CHARS = 200_000


def _cap_blocks(extraction: Extraction | None) -> Extraction | None:
    if extraction is None:
        return None
    kept: list[ExtractedBlock] = []
    budget = MAX_TEXT_CHARS
    for block in extraction.blocks:
        if budget <= 0:
            break
        if len(block.text) > budget:
            block = ExtractedBlock(block.text[:budget],
                                   {**block.locator, "truncated": True})
        budget -= len(block.text)
        kept.append(block)
    return Extraction(extraction.parser, kept)


def extract_text(data: bytes, filename: str) -> Extraction | None:
    """Returns None for unsupported formats."""
    name = filename.lower()
    if name.endswith(".pdf"):
        return _cap_blocks(_extract_pdf(data))
    if name.endswith(".docx"):
        return _cap_blocks(_extract_docx(data))
    if any(name.endswith(sfx) for sfx in TEXT_SUFFIXES):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return _cap_blocks(
            Extraction("plain", [ExtractedBlock(text, {"type": "file"})]))
    return None


def _extract_pdf(data: bytes) -> Extraction | None:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        blocks = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(ExtractedBlock(text, {"type": "page", "page": page_number}))
        return Extraction("pypdf", blocks)
    except Exception:
        return None


def _extract_docx(data: bytes) -> Extraction | None:
    import docx
    try:
        document = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
        if not text:
            return Extraction("python-docx", [])
        return Extraction("python-docx", [ExtractedBlock(text, {"type": "document"})])
    except Exception:
        return None


def chunk_blocks(blocks: list[ExtractedBlock], size: int = 1500,
                 overlap: int = 200) -> list[ExtractedBlock]:
    chunks: list[ExtractedBlock] = []
    for block in blocks:
        text = block.text
        if len(text) <= size:
            chunks.append(block)
            continue
        start = 0
        part = 0
        while start < len(text):
            piece = text[start : start + size]
            chunks.append(ExtractedBlock(piece, {**block.locator, "part": part}))
            start += size - overlap
            part += 1
    return chunks
