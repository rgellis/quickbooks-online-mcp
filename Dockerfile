# Development and CI image. There is no Python toolchain requirement on the
# host: uv, Python 3.12 and every dependency live in here.
FROM python:3.12-slim

# git is needed at install time: the SDK is pulled from a repository rather
# than PyPI. libatomic1 is required by pyright's bundled node binary.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git libatomic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra dev --no-install-project || true

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --extra dev

CMD ["bash"]
