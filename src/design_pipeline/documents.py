"""Project document ingestion for the MVP text-based BRD boundary."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree
from pathlib import Path

from pydantic import BaseModel, Field

from .storage import atomic_write


class DocumentSource(BaseModel):
    filename: str
    path: str
    media_type: str = "text/markdown"
    content: str = Field(min_length=1)


class DocumentReader:
    """Locate or persist a text BRD without coupling the runtime to a UI."""

    _SUPPORTED = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".rst": "text/x-rst",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    }

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def read_brd(self) -> DocumentSource | None:
        candidates = [
            self.project_root / ".design" / "input" / "BRD.md",
            self.project_root / "BRD.md",
            self.project_root / "requirements.md",
        ]
        for path in candidates:
            if path.exists():
                return self.read(path)
        return None

    def read(self, path: Path) -> DocumentSource:
        return self.read_bytes(path.read_bytes(), path.name, path=str(path))

    def read_bytes(self, data: bytes, filename: str, *, path: str | None = None) -> DocumentSource:
        """Extract BRD text from an accepted source without retaining the upload."""
        suffix = Path(filename).suffix.lower()
        if suffix not in self._SUPPORTED:
            supported = ", ".join(sorted(self._SUPPORTED))
            raise ValueError(f"unsupported document type {suffix or '<none>'}; supported types: {supported}")
        if suffix == ".docx":
            content = self._extract_docx_text(data)
        elif suffix == ".pdf":
            content = self._extract_pdf_text(data)
        elif suffix in (".xlsx", ".xlsm"):
            content = self._extract_xlsx_text(data)
        else:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"document is not valid UTF-8 text: {filename}") from exc
        if not content.strip():
            raise ValueError(f"document is empty: {filename}")
        return DocumentSource(filename=filename, path=path or filename, media_type=self._SUPPORTED[suffix], content=content)

    @staticmethod
    def _extract_docx_text(data: bytes) -> str:
        """Read paragraph text from a standard DOCX package using the OOXML body."""
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as document:
                xml = document.read("word/document.xml")
            root = ElementTree.fromstring(xml)
        except (KeyError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
            raise ValueError("invalid .docx file; Word document content could not be read") from exc
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(f"{namespace}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if text.strip():
                paragraphs.append(text.strip())
        return "\n\n".join(paragraphs)

    @staticmethod
    def _extract_pdf_text(data: bytes) -> str:
        """Extract text from a PDF document across all readable pages."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise ValueError(f"invalid .pdf file; PDF document content could not be read: {exc}") from exc
        content = "\n\n".join(p.strip() for p in pages if p.strip())
        if not content.strip():
            raise ValueError("PDF document contains no readable text (it may be scanned or image-only)")
        return content

    @staticmethod
    def _extract_xlsx_text(data: bytes) -> str:
        """Read cell text from a standard XLSX/XLSM package -- each sheet
        rendered as `" | "`-joined rows (blank trailing cells and fully
        blank rows dropped) under a `## Sheet: <name>` heading -- using the
        same hand-rolled-OOXML approach as `_extract_docx_text` rather than
        a spreadsheet-parsing dependency. Not a faithful grid render (no
        column alignment, merged cells, formulas' source expressions, or
        formatting), but readable enough for an agent to use as reference
        text, same tradeoff `_extract_docx_text` already makes for Word."""
        ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        ns_rel_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ns_pkg_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as workbook:
                names = set(workbook.namelist())
                workbook_xml = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
                rel_targets: dict[str, str] = {}
                if "xl/_rels/workbook.xml.rels" in names:
                    rels_xml = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
                    rel_targets = {rel.get("Id"): rel.get("Target") for rel in rels_xml.iter(f"{ns_pkg_rel}Relationship")}
                shared_strings: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    sst_xml = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
                    shared_strings = ["".join(node.text or "" for node in si.iter(f"{ns_main}t")) for si in sst_xml.iter(f"{ns_main}si")]

                sheets: list[tuple[str, str]] = []
                for sheet in workbook_xml.iter(f"{ns_main}sheet"):
                    target = rel_targets.get(sheet.get(ns_rel_id) or "")
                    member = target if (target or "").startswith("xl/") else f"xl/{target}" if target else None
                    if member and member in names:
                        sheets.append((sheet.get("name") or member, member))

                blocks: list[str] = []
                for sheet_name, member in sheets:
                    sheet_xml = ElementTree.fromstring(workbook.read(member))
                    rows: list[str] = []
                    for row in sheet_xml.iter(f"{ns_main}row"):
                        cells = [DocumentReader._xlsx_cell_text(cell, shared_strings, ns_main) for cell in row.iter(f"{ns_main}c")]
                        while cells and not cells[-1].strip():
                            cells.pop()
                        if any(cell.strip() for cell in cells):
                            rows.append(" | ".join(cells))
                    if rows:
                        blocks.append(f"## Sheet: {sheet_name}\n" + "\n".join(rows))
        except (KeyError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
            raise ValueError("invalid .xlsx file; spreadsheet content could not be read") from exc
        return "\n\n".join(blocks)

    @staticmethod
    def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str], ns_main: str) -> str:
        cell_type = cell.get("t")
        if cell_type == "inlineStr":
            inline = cell.find(f"{ns_main}is")
            return "".join(node.text or "" for node in inline.iter(f"{ns_main}t")) if inline is not None else ""
        value_el = cell.find(f"{ns_main}v")
        value = value_el.text if value_el is not None else None
        if value is None:
            return ""
        if cell_type == "s":
            try:
                return shared_strings[int(value)]
            except (ValueError, IndexError):
                return ""
        if cell_type == "b":
            return "TRUE" if value == "1" else "FALSE"
        return value

    def ingest_brd(self, source: Path) -> DocumentSource:
        document = self.read(source)
        destination = self.project_root / ".design" / "input" / "BRD.md"
        atomic_write(destination, document.content)
        return DocumentSource(filename="BRD.md", path=str(destination), media_type="text/markdown", content=document.content)

    def ingest_bytes(self, data: bytes, filename: str) -> DocumentSource:
        document = self.read_bytes(data, filename)
        destination = self.project_root / ".design" / "input" / "BRD.md"
        atomic_write(destination, document.content)
        return DocumentSource(filename=filename, path=str(destination), media_type="text/markdown", content=document.content)

    def ingest_text(self, content: str, filename: str = "BRD.md") -> DocumentSource:
        suffix = Path(filename).suffix.lower()
        if suffix in {".docx", ".pdf", ".xlsx", ".xlsm"}:
            format_name = {".docx": "Word documents", ".pdf": "PDF documents", ".xlsx": "Excel documents", ".xlsm": "Excel documents"}[suffix]
            raise ValueError(f"{format_name} must be uploaded as binary content")
        if suffix not in self._SUPPORTED:
            supported = ", ".join(sorted(self._SUPPORTED))
            raise ValueError(f"unsupported document type {suffix or '<none>'}; supported types: {supported}")
        if not content.strip():
            raise ValueError("document is empty")
        destination = self.project_root / ".design" / "input" / "BRD.md"
        atomic_write(destination, content)
        return DocumentSource(filename=filename, path=str(destination), media_type=self._SUPPORTED[suffix], content=content)
