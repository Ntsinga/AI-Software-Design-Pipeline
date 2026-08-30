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
        if suffix == ".docx":
            raise ValueError("Word documents must be uploaded as binary content")
        if suffix not in self._SUPPORTED:
            supported = ", ".join(sorted(self._SUPPORTED))
            raise ValueError(f"unsupported document type {suffix or '<none>'}; supported types: {supported}")
        if not content.strip():
            raise ValueError("document is empty")
        destination = self.project_root / ".design" / "input" / "BRD.md"
        atomic_write(destination, content)
        return DocumentSource(filename=filename, path=str(destination), media_type=self._SUPPORTED[suffix], content=content)
