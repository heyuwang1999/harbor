from prometheus_client import Counter, Histogram

CHAT_TURNS = Counter(
    "harbor_chat_turns_total", "Chat turns by outcome and channel", ["answer_type", "channel"]
)
CHAT_TTFT = Histogram(
    "harbor_chat_ttft_seconds",
    "Time to first answer token",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8),
)
CHAT_DURATION = Histogram(
    "harbor_chat_duration_seconds", "Full chat turn duration", buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10)
)
RETRIEVAL_STAGE = Histogram(
    "harbor_retrieval_stage_seconds",
    "Retrieval stage duration",
    ["stage"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
CITATIONS_DROPPED = Counter(
    "harbor_citations_dropped_total", "Citation markers pointing at unsupplied sources"
)
LINKS_STRIPPED = Counter(
    "harbor_links_stripped_total", "Links removed from answers (possible exfiltration attempts)"
)
