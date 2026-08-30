from pathlib import Path
from zipfile import ZipFile

import pytest

from design_pipeline.documents import DocumentReader


def word_document_bytes(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    import io

    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_ingest_brd_extracts_supported_text_and_persists(runtime, tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("# BR-017\nHigh-risk reports require Director approval.", encoding="utf-8")
    document = runtime.ingest_brd(source)
    assert document.filename == "BRD.md"
    assert (tmp_path / ".design" / "input" / "BRD.md").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_reader_rejects_unsupported_documents(tmp_path: Path):
    source = tmp_path / "requirements.pdf"
    source.write_bytes(b"not parsed in the MVP")
    with pytest.raises(ValueError, match="unsupported document type"):
        DocumentReader(tmp_path).read(source)


def test_reader_rejects_invalid_word_documents(tmp_path: Path):
    source = tmp_path / "requirements.docx"
    source.write_bytes(b"not a Word document")
    with pytest.raises(ValueError, match="invalid .docx file"):
        DocumentReader(tmp_path).read(source)


def test_reader_extracts_word_document_and_ingests_as_brd(runtime, tmp_path: Path):
    source = tmp_path / "requirements.docx"
    source.write_bytes(word_document_bytes("Business Requirements", "BR-042: Approvals must be auditable."))
    document = DocumentReader(tmp_path).read(source)
    assert document.media_type.endswith("document")
    assert "BR-042" in document.content

    ingested = runtime.ingest_brd_bytes(source.read_bytes(), source.name)
    assert ingested.filename == "requirements.docx"
    assert "Approvals must be auditable" in (tmp_path / ".design" / "input" / "BRD.md").read_text(encoding="utf-8")
