from harbor_api.core.text import normalize
from harbor_api.retrieval.glossary import expand


def test_colloquial_cantonese_expands_to_formal_document_wording() -> None:
    query, added = expand(normalize("八號風球使唔使返工？"))

    assert "台风信号" in query
    assert "上班" in query
    assert added


def test_english_abbreviations_expand_to_chinese_terms() -> None:
    query, added = expand(normalize("MPF 供幾多？"))

    assert "强积金" in query
    assert added == ["强积金", "供款"]


def test_query_without_glossary_terms_is_unchanged() -> None:
    normalized = normalize("年假有幾多日？")

    assert expand(normalized) == (normalized, [])


def test_terms_already_present_are_not_duplicated() -> None:
    query, added = expand(normalize("黑雨同黑色暴雨警告"))

    assert added == []
    assert query.count("黑色暴雨警告") == 1
