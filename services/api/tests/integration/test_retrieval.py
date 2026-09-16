"""Retrieval behaviour that the demo depends on, and the access rules that protect it."""

from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from harbor_api.core.config import Settings
from harbor_api.demo.seed import seed
from harbor_api.llm.registry import embedding_client
from harbor_api.retrieval.access import AccessScope
from harbor_api.retrieval.search import hybrid_search
from harbor_api.retrieval.types import RetrievalResult

ScopeFactory = Callable[[str], AccessScope]


async def search(
    session: AsyncSession, settings: Settings, scope: AccessScope, query: str
) -> RetrievalResult:
    return await hybrid_search(
        session,
        scope=scope,
        query=query,
        settings=settings,
        embedder=embedding_client(settings),
    )


@pytest.mark.parametrize(
    ("query", "expected_snippet"),
    [
        ("請病假要唔要醫生紙？", "醫生證明書"),  # Cantonese colloquial
        ("病假津貼需要什麼證明", "醫生證明書"),  # Traditional
        ("年假天数", "有薪年假"),  # Simplified, against Traditional source text
        ("八號風球使唔使返工？", "颱風信號"),
        ("probation notice period", "Probation lasts three months"),  # English
        ("vegan bread options", "vegan breads"),  # distinctive terms; see the note below
    ],
)
async def test_staff_questions_retrieve_the_right_passage(
    owner_session: AsyncSession,
    settings: Settings,
    scope_factory: ScopeFactory,
    query: str,
    expected_snippet: str,
) -> None:
    result = await search(owner_session, settings, scope_factory("staff"), query)

    assert result.answerable, f"expected an answerable result for {query!r}"
    # Recall, not rank: with deterministic mock embeddings only BM25 carries signal, so
    # top-1 precision is not meaningful until M1b adds real embeddings and a reranker.
    # English queries also carry stopwords ("what are the ...") because the Jieba
    # tokenizer does not strip them, which dilutes scores — tracked for ADR-0002.
    assert expected_snippet in "\n".join(candidate.text for candidate in result.context)


async def test_cross_language_retrieval_is_a_known_gap_under_mock_embeddings(
    owner_session: AsyncSession, settings: Settings, scope_factory: ScopeFactory
) -> None:
    """An English question whose answer exists only in a Chinese document.

    Jieba tokenisation cannot bridge the languages, and mock embeddings carry no meaning,
    so this fails today by design. Real multilingual embeddings (M1b) are what close it;
    this test documents the boundary rather than pretending it works.
    """
    result = await search(
        owner_session, settings, scope_factory("staff"), "monthly phone allowance"
    )

    assert "五百港元" not in "\n".join(candidate.text for candidate in result.context)


async def test_out_of_corpus_question_is_not_answerable(
    owner_session: AsyncSession, settings: Settings, scope_factory: ScopeFactory
) -> None:
    result = await search(owner_session, settings, scope_factory("staff"), "邊個發明咗小籠包？")

    # Mock embeddings still return nearest neighbours, so the gate must reject on scores
    # rather than on an empty candidate list.
    assert result.candidates
    assert not result.answerable
    assert result.context == []


@pytest.mark.parametrize(
    "query",
    [
        "薪酬級別範圍",  # restricted document
        "分店經理月薪",  # restricted document
        "報銷交通費的期限",  # internal document
        "請病假要唔要醫生紙？",  # internal document
    ],
)
async def test_visitor_never_sees_internal_or_restricted_passages(
    owner_session: AsyncSession, settings: Settings, scope_factory: ScopeFactory, query: str
) -> None:
    result = await search(owner_session, settings, scope_factory("visitor"), query)

    # Assert on the raw candidate list, not the answer: a leak must be impossible before
    # any model sees the text.
    assert result.candidates, "vector search should still return public neighbours"
    assert {candidate.visibility for candidate in result.candidates} == {"public"}


async def test_staff_cannot_see_restricted_but_management_can(
    owner_session: AsyncSession, settings: Settings, scope_factory: ScopeFactory
) -> None:
    query = "分店經理的月薪範圍是多少？"

    staff = await search(owner_session, settings, scope_factory("staff"), query)
    management = await search(owner_session, settings, scope_factory("management"), query)

    # Staff may still get an answer from documents they are allowed to read; what must
    # never happen is restricted content, or the salary figures, reaching them.
    assert "restricted" not in {candidate.visibility for candidate in staff.candidates}
    staff_text = "\n".join(candidate.text for candidate in staff.context)
    assert "22,000" not in staff_text
    assert management.answerable
    assert "restricted" in {candidate.visibility for candidate in management.context}
    assert "22,000" in "\n".join(candidate.text for candidate in management.context)


async def test_retrieval_records_per_stage_evidence(
    owner_session: AsyncSession, settings: Settings, scope_factory: ScopeFactory
) -> None:
    result = await search(owner_session, settings, scope_factory("staff"), "強積金供款比例")

    top = result.context[0]
    assert top.bm25_rank == 1
    assert top.bm25_score and top.bm25_score > 0
    assert top.vector_rank is not None  # every chunk is also scored by the vector search
    assert result.strategy["vector_search"] == "exact"  # small corpus → exact (ADR-0001)
    assert set(result.timings_ms) == {"embed", "bm25", "vector"}


async def test_seed_is_idempotent(settings: Settings, seeded: None) -> None:
    report = await seed(settings)

    assert report.documents_indexed == 0
    assert report.documents_skipped == 6
