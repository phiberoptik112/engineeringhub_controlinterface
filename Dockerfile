# Full Engineering Hub image for running the orchestrator on a Linux server.
# No MLX (Apple Silicon only) — supports Anthropic and Ollama backends.
#
# Build:
#   docker build -t engineering-hub:latest .
#
# Run:
#   docker run --rm \
#     -v ~/.config/engineering-hub/config.yaml:/app/config.yaml:ro \
#     -v ~/org-roam/engineering-hub:/workspace \
#     -e ENGINEERING_HUB_ANTHROPIC_API_KEY=sk-... \
#     --network engineering-hub-net \
#     engineering-hub:latest run-once

FROM python:3.11-slim AS base

LABEL maintainer="Engineering Hub"

RUN groupadd -r hubuser && useradd -r -g hubuser -d /home/hubuser -s /sbin/nologin hubuser

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY skills/ ./skills/
COPY config/ ./config/

RUN pip install --no-cache-dir -e . \
    && rm -rf /root/.cache/pip

RUN mkdir -p /workspace && chown hubuser:hubuser /workspace

USER hubuser

ENTRYPOINT ["engineering-hub"]
CMD ["--help"]
