from harbor_api.ingestion.chunking import chunk_markdown

# Section bodies are deliberately longer than the merge threshold so each keeps its own
# heading path; test_small_sections_merge covers the short case.
FILLER = "This sentence exists to push the section past the minimum chunk size. " * 4

DOCUMENT = f"""# Handbook

Introduction. {FILLER}

## Sick Leave

An employee absent for four or more consecutive days receives a sickness allowance. {FILLER}

### Medical Certificate

A certificate from a registered doctor is required. {FILLER}

## Tiny
x
"""


def test_chunks_carry_their_heading_path() -> None:
    chunks = chunk_markdown(DOCUMENT, title="Handbook")

    paths = [chunk.heading_path for chunk in chunks]
    assert ["Handbook"] in paths
    assert ["Handbook", "Sick Leave"] in paths
    assert ["Handbook", "Sick Leave", "Medical Certificate"] in paths


def test_index_text_includes_headings_but_display_text_does_not() -> None:
    chunk = next(c for c in chunk_markdown(DOCUMENT) if c.heading_path[-1:] == ["Sick Leave"])

    assert "Sick Leave" not in chunk.text
    assert "sick leave" in chunk.index_text.lower()  # normalised copy keeps the heading


def test_embed_text_prepends_document_title_and_headings() -> None:
    chunk = next(c for c in chunk_markdown(DOCUMENT, title="Handbook") if "registered" in c.text)

    assert chunk.embed_text.startswith("Handbook")
    assert "Medical Certificate" in chunk.embed_text


def test_small_sections_merge_into_the_previous_chunk_keeping_their_heading() -> None:
    chunks = chunk_markdown(DOCUMENT)

    merged = next(chunk for chunk in chunks if "\nx" in chunk.text)
    assert "Tiny" in merged.text  # the absorbed heading stays searchable and citable
    assert not any(chunk.text.strip() == "x" for chunk in chunks)


def test_long_sections_split_on_paragraph_boundaries() -> None:
    body = "\n\n".join(f"Paragraph number {index} with several words in it." for index in range(40))

    chunks = chunk_markdown(f"# Title\n\n{body}", max_tokens=60)

    assert len(chunks) > 1
    assert all(chunk.token_count <= 120 for chunk in chunks)
    assert all(not chunk.text.startswith("with several") for chunk in chunks)


def test_ordinals_are_contiguous() -> None:
    chunks = chunk_markdown(DOCUMENT)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
