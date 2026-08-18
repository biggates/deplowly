FROM ghcr.io/astral-sh/uv:python3.12-alpine AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
# copy project metadata first for better layer caching
COPY pyproject.toml ./
COPY src ./src
RUN uv sync --no-install-project --no-dev
COPY . .
RUN uv sync --no-dev

FROM python:3.12-alpine AS runner
# orjson / kubernetes_lite 的 Rust 扩展依赖 libstdc++，alpine 默认不提供
RUN apk add --no-cache libstdc++
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    LOG_LEVEL=INFO
ENTRYPOINT ["deplowly"]
CMD ["/etc/deplowly/config.yaml"]
