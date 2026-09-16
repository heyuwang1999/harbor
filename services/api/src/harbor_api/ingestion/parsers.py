"""Turn an uploaded file into markdown that `chunk_markdown` can split.

Every parser produces markdown with headings intact, because headings are what the chunker
splits on and what BM25 indexes (ADR-0001). Lightweight libraries by default — pypdf,
python-docx, BeautifulSoup — keep the image small and ingestion fast; Docling is an
optional profile for scanned pages and complex tables (ADR-0002).

A parser that cannot extract text raises `UnparsableDocument` with a message written for
the person who uploaded the file, not for a log.
"""

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bs4 import BeautifulSoup

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Extension → sniffed magic bytes. Both must agree: an extension alone is a claim by the
# uploader, and some parsers will happily produce nonsense from the wrong format.
MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04",),  # DOCX is a zip container
}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}


class UnparsableDocument(Exception):
    """The file cannot be indexed; the message is shown to the uploader."""


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    page_count: int | None = None


class Parser(Protocol):
    def parse(self, data: bytes, filename: str) -> ParsedDocument: ...


def validate_upload(data: bytes, filename: str) -> str:
    """Check size, extension and magic bytes. Returns the normalised extension."""
    if not data:
        raise UnparsableDocument("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnparsableDocument(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    extension = Path(filename).suffix.lower()
    if extension not in MAGIC and extension not in TEXT_EXTENSIONS:
        supported = ", ".join(sorted(set(MAGIC) | TEXT_EXTENSIONS))
        raise UnparsableDocument(
            f"Unsupported file type '{extension or filename}'. Supported: {supported}"
        )
    if extension in MAGIC and not data.startswith(MAGIC[extension]):
        raise UnparsableDocument(
            f"The file does not look like a real {extension.lstrip('.').upper()} file."
        )
    return extension


class TextParser:
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        return ParsedDocument(markdown=_decode(data).strip())


class HtmlParser:
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        soup = BeautifulSoup(_decode(data), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()

        lines: list[str] = []
        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td"]):
            text = element.get_text(" ", strip=True)
            if not text:
                continue
            name = element.name
            if name.startswith("h") and name[1:].isdigit():
                lines.append(f"\n{'#' * int(name[1:])} {text}\n")
            elif name == "li":
                lines.append(f"- {text}")
            else:
                lines.append(text)
        markdown = _collapse_blank_lines("\n".join(lines))
        if not markdown.strip():
            raise UnparsableDocument("No readable text found in the HTML file.")
        return ParsedDocument(markdown=markdown)


class PdfParser:
    """pypdf text extraction.

    A PDF with no text layer is a scan: pypdf returns empty strings and indexing it would
    create a document that answers nothing. Fail loudly and point at the Docling profile.
    """

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(data))
        except PdfReadError as error:
            raise UnparsableDocument(f"The PDF could not be read: {error}") from error
        if reader.is_encrypted:
            raise UnparsableDocument(
                "The PDF is password-protected. Remove the password and retry."
            )

        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # a single broken page should not fail the document
                pages.append("")

        extracted = "\n\n".join(page.strip() for page in pages if page.strip())
        if len(extracted.strip()) < 40:
            raise UnparsableDocument(
                "No text layer found — this looks like a scanned PDF. "
                "Enable the docling parser profile to run OCR."
            )
        return ParsedDocument(
            markdown=_collapse_blank_lines(extracted), page_count=len(reader.pages)
        )


class DocxParser:
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        import docx

        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as error:
            raise UnparsableDocument(f"The Word file could not be read: {error}") from error

        lines: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style else ""
            if style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                lines.append(f"\n{'#' * min(int(level), 6)} {text}\n")
            elif style.startswith("title"):
                lines.append(f"\n# {text}\n")
            else:
                lines.append(text)

        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")

        markdown = _collapse_blank_lines("\n".join(lines))
        if not markdown.strip():
            raise UnparsableDocument("No readable text found in the Word file.")
        return ParsedDocument(markdown=markdown)


PARSERS: dict[str, Parser] = {
    ".md": TextParser(),
    ".markdown": TextParser(),
    ".txt": TextParser(),
    ".html": HtmlParser(),
    ".htm": HtmlParser(),
    ".pdf": PdfParser(),
    ".docx": DocxParser(),
}


def parse(data: bytes, filename: str) -> ParsedDocument:
    extension = validate_upload(data, filename)
    return PARSERS[extension].parse(data, filename)


BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def _plausibility(text: str) -> float:
    """Fraction of characters that look like real content.

    Legacy Chinese encodings decode each other's bytes without raising, so "it did not
    throw" is not evidence of the right encoding. CJK, ASCII and common punctuation count
    as plausible; anything else (private-use areas, stray control characters) does not.
    """
    if not text:
        return 0.0
    good = sum(
        1
        for char in text
        if (char.isascii() and (char.isprintable() or char.isspace()))
        or ("\u3000" <= char <= "\u9fff")  # CJK punctuation and ideographs
        or ("\uff00" <= char <= "\uffef")  # full-width forms
    )
    return good / len(text)


def _decode(data: bytes) -> str:
    """Decode text uploads, including the Big5 and GB18030 files HK clients still export.

    UTF-16 is only tried when a byte-order mark says so: without one it happily decodes
    Big5 bytes into nonsense instead of raising.
    """
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")

    candidates: list[tuple[float, str]] = []
    for encoding in ("utf-8", "big5", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = _plausibility(text)
        if encoding == "utf-8" and score > 0.99:
            return text  # unambiguous: nothing else decodes valid UTF-8 better
        candidates.append((score, text))

    if not candidates:
        return data.decode("utf-8", errors="replace")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()
