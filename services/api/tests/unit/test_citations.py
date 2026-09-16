import uuid

from harbor_api.generation.citations import validate_answer
from harbor_api.retrieval.types import Candidate


def candidate(text: str, score: float = 0.03) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        ordinal=0,
        text=text,
        heading_path=["Handbook", "Sick Leave"],
        visibility="internal",
        document_title="Handbook",
        fused_score=score,
    )


def test_valid_citations_resolve_to_the_retrieved_passage() -> None:
    context = [candidate("A certificate is required."), candidate("Annual leave is seven days.")]

    result = validate_answer("You need a certificate [S1] and leave accrues [S2].", context)

    assert [citation["marker"] for citation in result.citations] == ["S1", "S2"]
    assert result.citations[0]["chunk_id"] == str(context[0].chunk_id)
    assert result.is_grounded


def test_citations_outside_the_supplied_range_are_dropped() -> None:
    context = [candidate("A certificate is required.")]

    result = validate_answer("Definitely true [S1] and also this [S7].", context)

    assert result.dropped_citations == 1
    assert "[S7]" not in result.text
    assert [citation["marker"] for citation in result.citations] == ["S1"]


def test_links_and_images_are_stripped() -> None:
    context = [candidate("A certificate is required.")]

    result = validate_answer(
        "See [the policy](https://evil.example/leak?q=secret) ![x](https://evil.example/p.png) "
        "and https://evil.example/raw [S1]",
        context,
    )

    assert "evil.example" not in result.text
    assert "the policy" in result.text  # link text survives, the URL does not
    assert result.stripped_links == 3


def test_an_answer_with_no_valid_citations_is_not_grounded() -> None:
    result = validate_answer("I am confident this is right.", [candidate("Unrelated.")])

    assert not result.is_grounded
    assert result.confidence == 0.0


def test_confidence_reflects_the_strength_of_cited_evidence() -> None:
    context = [candidate("strong", score=0.05), candidate("weak", score=0.01)]

    strong = validate_answer("Answer [S1]", context)
    weak = validate_answer("Answer [S2]", context)

    assert strong.confidence == 1.0
    assert weak.confidence < strong.confidence
