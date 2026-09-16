import uuid

import pytest

from harbor_api.models.content import Visibility
from harbor_api.retrieval.access import scope_for_role
from harbor_api.retrieval.fuse import reciprocal_rank_fusion
from harbor_api.retrieval.types import Candidate

GROUPS = {"everyone": uuid.uuid4(), "staff": uuid.uuid4(), "management": uuid.uuid4()}
TENANT = uuid.uuid4()


def candidate(name: str) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_OID, name),
        document_id=uuid.uuid4(),
        ordinal=0,
        text=name,
        heading_path=[],
        visibility="public",
    )


def test_rrf_ranks_a_chunk_found_by_both_retrievers_above_single_hits() -> None:
    shared, lexical_only, vector_only = candidate("shared"), candidate("lex"), candidate("vec")
    lexical = [lexical_only, shared]
    semantic = [vector_only, candidate("shared")]

    fused = reciprocal_rank_fusion([lexical, semantic], k=60)

    assert fused[0].text == "shared"
    assert {c.text for c in fused} == {"shared", "lex", "vec"}


def test_rrf_keeps_evidence_from_both_retrievers_on_the_merged_candidate() -> None:
    lexical = candidate("shared")
    lexical.bm25_rank, lexical.bm25_score = 1, 12.5
    semantic = candidate("shared")
    semantic.vector_rank, semantic.similarity = 3, 0.42

    fused = reciprocal_rank_fusion([[lexical], [semantic]])

    assert fused[0].bm25_score == 12.5
    assert fused[0].similarity == 0.42


def test_visitor_scope_is_limited_to_public_documents_and_the_everyone_group() -> None:
    scope = scope_for_role(TENANT, "visitor", GROUPS)

    assert scope.visibilities == (Visibility.PUBLIC,)
    assert scope.principals == (GROUPS["everyone"],)


def test_management_scope_includes_restricted_documents() -> None:
    scope = scope_for_role(TENANT, "management", GROUPS)

    assert Visibility.RESTRICTED in scope.visibilities
    assert set(scope.principals) == set(GROUPS.values())


def test_scope_filter_binds_every_value_as_a_parameter() -> None:
    scope = scope_for_role(TENANT, "staff", GROUPS)

    # No interpolated literals: the fragment is safe to embed in a query string.
    assert str(TENANT) not in scope.sql_filter
    assert set(scope.params) == {"tenant_id", "visibilities", "principals"}


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        scope_for_role(TENANT, "root", GROUPS)
