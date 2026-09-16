"""Validate what the model produced against what was actually retrieved.

A model can cite a source that was never supplied, or emit a link that no document
contains. Both are checked here rather than trusted:

  - `[S#]` markers outside the supplied range are removed, and the surviving ones are
    resolved to chunk ids so the UI can open the exact passage.
  - Markdown links and images are stripped. Harbor's sources carry no URLs, so any URL in
    an answer was invented — and a rendered link is an exfiltration channel when a
    poisoned document asks the model to append the question to a URL.
"""

import re
from dataclasses import dataclass
from typing import Any

from harbor_api.retrieval.types import Candidate

CITATION = re.compile(r"\[S(\d+)\]")
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
BARE_URL = re.compile(r"https?://\S+")


@dataclass
class ValidatedAnswer:
    text: str
    citations: list[dict[str, Any]]
    dropped_citations: int
    stripped_links: int
    confidence: float

    @property
    def is_grounded(self) -> bool:
        return bool(self.citations)


def _strip_links(text: str) -> tuple[str, int]:
    stripped = 0

    def drop_image(_match: re.Match[str]) -> str:
        nonlocal stripped
        stripped += 1
        return ""

    def unwrap_link(match: re.Match[str]) -> str:
        nonlocal stripped
        stripped += 1
        return match.group(1)

    text = MARKDOWN_IMAGE.sub(drop_image, text)
    text = MARKDOWN_LINK.sub(unwrap_link, text)
    text, count = BARE_URL.subn("", text)
    return text, stripped + count


def validate_answer(raw: str, context: list[Candidate]) -> ValidatedAnswer:
    text, stripped_links = _strip_links(raw)

    cited_indexes: list[int] = []
    dropped = 0

    def keep_or_drop(match: re.Match[str]) -> str:
        nonlocal dropped
        index = int(match.group(1))
        if 1 <= index <= len(context):
            if index not in cited_indexes:
                cited_indexes.append(index)
            return match.group(0)
        dropped += 1
        return ""

    text = CITATION.sub(keep_or_drop, text).strip()

    citations = [
        {
            "marker": f"S{index}",
            "chunk_id": str(context[index - 1].chunk_id),
            "document_id": str(context[index - 1].document_id),
            "document_title": context[index - 1].document_title,
            "heading_path": context[index - 1].heading_path,
            "text": context[index - 1].text,
        }
        for index in cited_indexes
    ]

    # Heuristic: how much of the retrieval evidence the answer actually leaned on,
    # relative to the best passage retrieved. Calibrated against the eval set in M1b.
    best = max((candidate.fused_score for candidate in context), default=0.0)
    cited_scores = [context[index - 1].fused_score for index in cited_indexes]
    confidence = 0.0
    if cited_scores and best:
        confidence = min(1.0, (sum(cited_scores) / len(cited_scores)) / best)

    return ValidatedAnswer(
        text=text,
        citations=citations,
        dropped_citations=dropped,
        stripped_links=stripped_links,
        confidence=round(confidence, 4),
    )
