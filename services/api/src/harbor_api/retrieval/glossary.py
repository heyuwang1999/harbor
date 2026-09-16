"""Hong Kong query expansion.

ADR-0001 found the gap: BM25 cannot bridge everyday Hong Kong wording and the formal
wording documents use. "八號風球" never appears in a handbook that says "八號颱風信號",
and "MPF" never appears next to "強積金". Dense retrieval is the general answer, but it
needs real multilingual embeddings (M1b); until then — and afterwards, as a cheap boost —
an explicit glossary carries the terms people actually type.

Keys and values are written in Simplified, because expansion runs after OpenCC
normalisation (see core/text.normalize). Matching is substring-based and case-insensitive,
which is what Chinese without word boundaries needs.
"""

HK_GLOSSARY: dict[str, tuple[str, ...]] = {
    # Weather and attendance
    "风球": ("台风信号",),
    "挂波": ("台风信号",),
    "黑雨": ("黑色暴雨警告",),
    "返工": ("上班", "工作"),
    "收工": ("下班",),
    # Pay and benefits
    "mpf": ("强积金", "供款"),
    "人工": ("薪金", "工资"),
    "出粮": ("发薪", "工资"),
    "花红": ("年终花红", "奖金"),
    "ot": ("加班", "超时工作"),
    # Leave and medical
    "医生纸": ("医生证明书", "病假"),
    "病假纸": ("医生证明书",),
    "放假": ("年假", "假期"),
    "月经假": ("病假",),
    # Customer-facing
    "落单": ("订购", "订单"),
    "送货": ("送货服务", "运费"),
    "退钱": ("退款",),
}


def expand(normalized_query: str) -> tuple[str, list[str]]:
    """Return the query with glossary terms appended, plus the terms that were added."""
    haystack = normalized_query.lower()
    added: list[str] = []
    for term, expansions in HK_GLOSSARY.items():
        if term in haystack:
            added.extend(expansion for expansion in expansions if expansion not in haystack)
    if not added:
        return normalized_query, []
    return f"{normalized_query} {' '.join(added)}", added
