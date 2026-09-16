# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
# Pick up base-image security fixes (Trivy gates the build on HIGH/CRITICAL).
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 mock
WORKDIR /app
COPY --from=build --chown=mock:mock /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 MOCK_LLM_HOST=0.0.0.0 MOCK_LLM_PORT=8100
USER mock
EXPOSE 8100
CMD ["mock-llm"]
