"""Text utilities shared by ingestion and retrieval.

Chinese handling follows ADR-0001: index and query an OpenCC `hk2s`-normalised copy,
because Jieba's dictionary is Simplified-centric and mis-segments Traditional text.
"""

import math
import re
from functools import lru_cache
from typing import Literal

import opencc

Script = Literal["en", "zh-Hant", "zh-Hans"]

_CJK = re.compile(r"[㐀-鿿]")


@lru_cache(maxsize=1)
def _converter() -> opencc.OpenCC:
    return opencc.OpenCC("hk2s.json")


def normalize(text: str) -> str:
    """Hong Kong Traditional → Simplified, for indexing and querying only."""
    converted: str = _converter().convert(text)
    return converted


def detect_script(text: str) -> Script:
    """Cheap script detection: enough to pick the answer language and pin eval cases."""
    cjk = _CJK.findall(text)
    if len(cjk) < max(1, len(text) * 0.15):
        return "en"
    # If normalising changes characters, the input carried Traditional forms.
    return "zh-Hant" if normalize(text) != text else "zh-Hans"


def count_tokens(text: str) -> int:
    """Approximate token count (~4 chars/token, 1 token per CJK char).

    Deliberately dependency-free: it only sizes chunks and context budgets, and the
    real usage numbers come from the provider response.
    """
    cjk = len(_CJK.findall(text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))
