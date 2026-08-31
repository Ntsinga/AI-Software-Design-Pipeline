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


def pdf_document_bytes(*paragraphs: str) -> bytes:
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font_dict = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_dict)

    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): font_ref
        })
    })

    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for p in paragraphs:
        clean = p.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({clean}) Tj")
        ops.append("0 -15 Td")
    ops.append("ET")

    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    stream_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = stream_ref

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_ingest_brd_extracts_supported_text_and_persists(runtime, tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("# BR-017\nHigh-risk reports require Director approval.", encoding="utf-8")
    document = runtime.ingest_brd(source)
    assert document.filename == "BRD.md"
    assert (tmp_path / ".design" / "input" / "BRD.md").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_reader_rejects_unsupported_documents(tmp_path: Path):
    source = tmp_path / "requirements.xyz"
    source.write_bytes(b"not parsed format")
    with pytest.raises(ValueError, match="unsupported document type"):
        DocumentReader(tmp_path).read(source)


def test_reader_rejects_invalid_word_documents(tmp_path: Path):
    source = tmp_path / "requirements.docx"
    source.write_bytes(b"not a Word document")
    with pytest.raises(ValueError, match="invalid .docx file"):
        DocumentReader(tmp_path).read(source)


def test_reader_rejects_invalid_pdf_documents(tmp_path: Path):
    source = tmp_path / "requirements.pdf"
    source.write_bytes(b"not a valid PDF document")
    with pytest.raises(ValueError, match="invalid .pdf file"):
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


def test_reader_extracts_pdf_document_and_ingests_as_brd(runtime, tmp_path: Path):
    source = tmp_path / "requirements.pdf"
    source.write_bytes(pdf_document_bytes("Business Requirements", "BR-099: Multi-factor authentication is required."))
    document = DocumentReader(tmp_path).read(source)
    assert document.media_type == "application/pdf"
    assert "BR-099" in document.content

    ingested = runtime.ingest_brd_bytes(source.read_bytes(), source.name)
    assert ingested.filename == "requirements.pdf"
    assert "Multi-factor authentication is required" in (tmp_path / ".design" / "input" / "BRD.md").read_text(encoding="utf-8")
