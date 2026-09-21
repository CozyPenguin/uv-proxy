FROM ghcr.io/astral-sh/uv:0.6.14-python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

EXPOSE 8000 8888
CMD ["uv", "run", "uvicorn", "uv_proxy.main:app", "--host", "0.0.0.0", "--port", "8000"]
