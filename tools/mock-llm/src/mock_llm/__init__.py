import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "mock_llm.app:app",
        host=os.environ.get("MOCK_LLM_HOST", "127.0.0.1"),
        port=int(os.environ.get("MOCK_LLM_PORT", "8100")),
    )
