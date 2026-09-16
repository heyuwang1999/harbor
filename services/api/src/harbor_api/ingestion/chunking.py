"""Heading-aware markdown chunking.

Retrieval quality depends far more on chunk boundaries than on model choice, so chunks
follow the document's own structure: one chunk per section, split on paragraph
boundaries when a section is too long, and tiny sections merged into the next one.

Three texts come out of each chunk and they are deliberately different:
  - `text`      what the user sees and what gets cited: the body only.
  - `index_text` what BM25 indexes (after OpenCC normalisation): headings + body, so a
                 query matching a heading finds the passage under it.
  - `embed_text` what the embedding model sees: document title + heading path + body,
                 a "contextual chunk header" that disambiguates otherwise similar passages.

The 400-token target and the merge threshold are starting values; M1b compares chunking
variants on the eval set and records the winner in ADR-0002.
"""

import re
from dataclasses import dataclass

from harbor_api.core.text import count_tokens, normalize

# Bump when chunk boundaries change: it feeds the document content hash, so existing
# documents are re-chunked instead of silently keeping stale passages.
CHUNKER_VERSION = "2"

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
MAX_TOKENS = 400
MIN_TOKENS = 40


@dataclass(frozen=True)
class MarkdownChunk:
    ordinal: int
    heading_path: list[str]
    text: str
    index_text: str
    embed_text: str
    token_count: int


def _split_paragraphs(body: str, max_tokens: int) -> list[str]:
    """Pack paragraphs into pieces of at most `max_tokens`, never splitting a paragraph."""
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()):
        tokens = count_tokens(paragraph)
        if current and current_tokens + tokens > max_tokens:
            pieces.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(paragraph)
        current_tokens += tokens
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_markdown(
    markdown: str,
    *,
    title: str = "",
    max_tokens: int = MAX_TOKENS,
    min_tokens: int = MIN_TOKENS,
) -> list[MarkdownChunk]:
    sections: list[tuple[list[str], str]] = []
    heading_stack: list[str] = []
    body: list[str] = []

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append((list(heading_stack), content))
        body.clear()

    for line in markdown.splitlines():
        match = HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            del heading_stack[level - 1 :]
            heading_stack.append(match.group(2))
        else:
            body.append(line)
    flush()

    # Merge sections too small to stand alone (e.g. a heading with a single line), but keep
    # the absorbed heading inside the text: the chunk is attributed to the parent section,
    # so without it the sub-heading's terms would vanish from both the index and the
    # citation shown to the user. Never merge past max_tokens.
    merged: list[tuple[list[str], str]] = []
    for path, content in sections:
        if merged and count_tokens(content) < min_tokens:
            previous_path, previous_content = merged[-1]
            if count_tokens(previous_content) + count_tokens(content) <= max_tokens:
                heading = path[-1] if path and path != previous_path else ""
                absorbed = f"{heading}\n{content}" if heading else content
                merged[-1] = (previous_path, f"{previous_content}\n\n{absorbed}")
                continue
        merged.append((path, content))

    chunks: list[MarkdownChunk] = []
    for path, content in merged:
        for piece in _split_paragraphs(content, max_tokens):
            heading_line = " > ".join(path)
            chunks.append(
                MarkdownChunk(
                    ordinal=len(chunks),
                    heading_path=path,
                    text=piece,
                    index_text=normalize(f"{heading_line}\n{piece}" if heading_line else piece),
                    embed_text="\n".join(filter(None, [title, heading_line, piece])),
                    token_count=count_tokens(piece),
                )
            )
    return chunks
