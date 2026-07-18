FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8800
# Run the venv interpreter directly: `uv run` re-resolves the project at
# startup and needs PyPI, but App Runner instances with VPC egress have no
# internet access.
CMD ["/app/.venv/bin/python", "-m", "pis.serve"]
