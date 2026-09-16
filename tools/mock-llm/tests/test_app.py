import json
import math

from fastapi.testclient import TestClient

from mock_llm.app import app

client = TestClient(app)

CHAT = {"model": "mock-main", "messages": [{"role": "user", "content": "請病假要唔要醫生紙？"}]}


def test_chat_non_streaming_echoes_question_with_usage() -> None:
    response = client.post("/v1/chat/completions", json=CHAT)
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Mock answer to: 請病假要唔要醫生紙？"
    assert body["usage"]["prompt_tokens"] > 0
    assert body["usage"]["total_tokens"] == (
        body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )


def test_chat_streaming_reassembles_and_reports_usage() -> None:
    payload = {**CHAT, "stream": True, "stream_options": {"include_usage": True}}
    headers = {"x-mock-response": "Hello from Harbor [S1]"}
    with client.stream("POST", "/v1/chat/completions", json=payload, headers=headers) as response:
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]

    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "Hello from Harbor [S1]"
    assert chunks[-1]["usage"]["completion_tokens"] > 0


def test_json_response_format_returns_valid_json() -> None:
    payload = {**CHAT, "response_format": {"type": "json_object"}}
    content = client.post("/v1/chat/completions", json=payload).json()
    assert json.loads(content["choices"][0]["message"]["content"]) == {}


def test_forced_error_status_uses_openai_error_shape() -> None:
    response = client.post("/v1/chat/completions", json=CHAT, headers={"x-mock-status": "429"})
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_exceeded"


def test_embeddings_are_deterministic_normalised_and_sized() -> None:
    payload = {"model": "mock-embed", "input": ["hello", "你好"], "dimensions": 64}
    first = client.post("/v1/embeddings", json=payload).json()
    second = client.post("/v1/embeddings", json=payload).json()

    vectors = [item["embedding"] for item in first["data"]]
    assert vectors == [item["embedding"] for item in second["data"]]
    assert all(len(v) == 64 for v in vectors)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0) for v in vectors)
    assert vectors[0] != vectors[1]
