import json
from typing import Any


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event. The contract lives in docs/specs/chat-stream.md."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
