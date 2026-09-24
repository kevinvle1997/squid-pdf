# Reproduces the CI environment so `squidpdf` and its tests run the same way on
# any machine. Installs every extra, as CI does, so the API's dependencies are
# here once it exists.
FROM python:3.14-slim-bookworm
# Pinned so "the same environment as CI" doesn't drift between builds.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app

# Dependencies layer: only rebuilds when these change, not on every edit.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --all-extras --no-install-project

# Project layer: rebuilds on source changes; dependency layer stays cached.
COPY src/ src/
COPY tests/ tests/
COPY fixtures/ fixtures/
RUN uv sync --locked --all-extras

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["uv", "run", "pytest", "-q"]
