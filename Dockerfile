# Serves the API, and reproduces the CI environment so `squidpdf` and its tests
# run the same way on any machine: `docker run --rm squidpdf uv run pytest -q`.
# Installs every extra, as CI does.
FROM python:3.14-slim-bookworm
# Pinned so "the same environment as CI" doesn't drift between builds.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

# Uploaded PDFs are hostile input, so nothing here runs as root. /data holds the
# documents for their hour; a volume mounted there takes its owner from here.
RUN useradd --system --create-home squid && mkdir /app /data && chown squid /app /data
USER squid
WORKDIR /app

# Dependencies layer: only rebuilds when these change, not on every edit.
COPY --chown=squid pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --all-extras --no-install-project

# Project layer: rebuilds on source changes; dependency layer stays cached.
COPY --chown=squid src/ src/
COPY --chown=squid tests/ tests/
COPY --chown=squid fixtures/ fixtures/
RUN uv sync --locked --all-extras

ENV PATH="/app/.venv/bin:${PATH}" \
    SQUIDPDF_DATA=/data

EXPOSE 8000

# The image has no curl; the standard library asks instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --start-interval=1s \
    CMD ["python", "-c", "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# One process; all PDF work runs in its worker pool. Keep-alive outlasts Caddy's two idle
# minutes, so the proxy never reuses a connection the app has just closed.
CMD ["uvicorn", "squidpdf.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "130"]
