def main() -> None:
    import uvicorn

    uvicorn.run(
        "harbor_api.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container entrypoint
        port=8000,
    )
