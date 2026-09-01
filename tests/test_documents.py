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


def xlsx_document_bytes(sheet_name: str, rows: list[list[str]]) -> bytes:
    """Build a minimal, valid-enough XLSX package -- just the members
    `_extract_xlsx_text` actually reads (workbook.xml, its rels,
    sharedStrings.xml, one worksheet), same minimal-fixture approach as
    `word_document_bytes` above (which likewise skips [Content_Types].xml
    and the package-level _rels that a real Excel/Word export always
    includes but this parser never looks at)."""
    import io

    strings: list[str] = []

    def idx(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    columns = "ABCDEFGHIJ"
    row_xml = "".join(
        f'<row r="{r}">' + "".join(f'<c r="{columns[c]}{r}" t="s"><v>{idx(value)}</v></c>' for c, value in enumerate(row)) + "</row>"
        for r, row in enumerate(rows, start=1)
    )
    sheet_xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{row_xml}</sheetData></worksheet>'
    sst_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/sharedStrings.xml", sst_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
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


def test_reader_rejects_invalid_xlsx_documents(tmp_path: Path):
    source = tmp_path / "requirements.xlsx"
    source.write_bytes(b"not an Excel workbook")
    with pytest.raises(ValueError, match="invalid .xlsx file"):
        DocumentReader(tmp_path).read(source)


def test_reader_extracts_xlsx_document_including_shared_strings_and_blank_trimming(tmp_path: Path):
    source = tmp_path / "requirements.xlsx"
    rows = [
        ["Requirement ID", "Description", ""],  # trailing blank cell must be dropped
        ["BR-055", "Excel-based requirements must be extractable", ""],
        ["", "", ""],  # fully blank row must be dropped entirely
        ["BR-056", "Reused strings must resolve via the shared string table", ""],
    ]
    source.write_bytes(xlsx_document_bytes("Requirements", rows))
    document = DocumentReader(tmp_path).read(source)
    assert document.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "## Sheet: Requirements" in document.content
    assert "BR-055" in document.content and "Excel-based requirements must be extractable" in document.content
    assert "BR-056" in document.content and "shared string table" in document.content
    # Trailing blank cells dropped, so no row ends with a dangling " | ".
    assert not any(line.rstrip().endswith("|") for line in document.content.splitlines())
    # The fully-blank row contributed nothing.
    assert document.content.count("BR-0") == 2


def test_reference_upload_accepts_xlsx_supporting_documents(runtime, tmp_path: Path):
    """The user-facing ask this covers: supporting docs (per-stage
    reference attachments) can now be Excel files, not just Word/PDF/text."""
    source = tmp_path / "field-mapping.xlsx"
    source.write_bytes(xlsx_document_bytes("Fields", [["Screen", "Label"], ["audit_plans_tab", "Annual Audit Plans"]]))
    artifact = runtime.add_reference_bytes("mockup", source.read_bytes(), source.name)
    entries = artifact.content
    assert any(entry["filename"] == "field-mapping.xlsx" and "Annual Audit Plans" in entry["content"] for entry in entries)
