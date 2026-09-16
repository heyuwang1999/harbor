import uuid

from harbor_api.generation.prompt import SYSTEM_PROMPT, build_messages, format_sources, refusal_for
from harbor_api.retrieval.types import Candidate


def candidate(title: str = "Handbook", heading: list[str] | None = None) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        ordinal=0,
        text="A certificate from a registered doctor is required.",
        heading_path=heading if heading is not None else ["Handbook", "Sick Leave"],
        visibility="internal",
        document_title=title,
    )


def test_sources_are_numbered_and_wrapped_in_tags() -> None:
    rendered = format_sources([candidate(), candidate(title="FAQ")])

    assert '<source id="S1"' in rendered
    assert '<source id="S2"' in rendered
    assert rendered.count("</source>") == 2


def test_heading_separators_cannot_break_out_of_the_opening_tag() -> None:
    # Heading paths are rendered into attributes; an unescaped ">" would end the tag and
    # leak the rest of the metadata into the answer body.
    rendered = format_sources([candidate(title='Hand">book', heading=["A > B", "C"])])

    opening_tag = rendered.split("\n", 1)[0]
    assert opening_tag.endswith('">')
    assert opening_tag.count(">") == 1  # the ">" in the heading path did not end the tag
    assert opening_tag == '<source id="S1" document="Hand\'/book" section="A / B / C">'


def test_system_prompt_is_a_stable_prefix_for_prompt_caching() -> None:
    first = build_messages("Question one", [candidate()])
    second = build_messages("Question two", [candidate(title="Other")])

    assert first[0] == second[0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_history_is_placed_between_system_prompt_and_the_new_question() -> None:
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]

    messages = build_messages("now", [candidate()], history)

    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert "now" in messages[-1]["content"]


def test_refusal_matches_the_script_of_the_question() -> None:
    assert "找不到" in refusal_for("請病假要唔要醫生紙？")
    assert "找不到" in refusal_for("强积金供款比例是多少？")
    assert refusal_for("Who invented dim sum?").startswith("I could not find")
