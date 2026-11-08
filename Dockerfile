# Reviewer console for the support triage demo. Offline by default (fake provider).
FROM python:3.13-slim

# uv, pinned to the minor version used for uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Code, pack and eval set. The fixture tickets seed the console on first start.
# eval/ includes the committed reports in eval/results/, so the Monitor tab can show the latest one.
COPY src/ src/
COPY pack/ pack/
COPY eval/ eval/
COPY tests/fixtures/tickets.jsonl tests/fixtures/tickets.jsonl
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
USER app
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]
CMD ["python", "-m", "triage_agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
