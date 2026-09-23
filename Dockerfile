# Reproduces the engine/CLI environment from CI so `squidpdf` and its tests run
# the same way on any machine. No API or frontend here yet — see HANDOFF.md
# gap #6; this covers what actually exists today.
FROM python:3.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Dependencies layer: only rebuilds when these change, not on every edit.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-install-project

# Project layer: rebuilds on source changes; dependency layer stays cached.
COPY src/ src/
COPY tests/ tests/
COPY fixtures/ fixtures/
RUN uv sync --locked

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["uv", "run", "pytest", "-q"]
