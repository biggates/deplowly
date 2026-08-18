# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:latest AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
# copy project metadata first for better layer caching
COPY pyproject.toml ./
COPY src ./src
RUN uv sync --no-install-project --no-dev
COPY . .
RUN uv sync --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    LOG_LEVEL=INFO
ENTRYPOINT ["deplowly"]
CMD ["/etc/deplowly/config.yaml"]
