# Two images from one file. The default serves the API: the app and what it
# needs, nothing else. `--target test` reproduces the CI environment, dev tools
# and tests/ too, so `squidpdf` and its tests run the same way on any machine:
#   docker build --target test -t squidpdf-test . && docker run --rm squidpdf-test
FROM python:3.14-slim-bookworm AS base
# Pinned so "the same environment as CI" doesn't drift between builds.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

# Uploaded PDFs are hostile input, so nothing here runs as root. /data holds the
# documents for their hour; a volume mounted there takes its owner from here.
# 999 by name: what useradd --system gave it before it was pinned, so a volume
# made then stays writable, and a rebuild can't hand it another number.
RUN groupadd --system --gid 999 squid \
    && useradd --system --uid 999 --gid squid --create-home squid \
    && mkdir /app /data && chown squid /app /data
USER squid
WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    SQUIDPDF_DATA=/data


# The suite as CI runs it: every extra, the dev tools, tests/ and fixtures/.
FROM base AS test

# Dependencies layer: only rebuilds when these change, not on every edit.
COPY --chown=squid pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --all-extras --no-install-project

# Project layer: rebuilds on source changes; dependency layer stays cached.
COPY --chown=squid src/ src/
COPY --chown=squid tests/ tests/
COPY --chown=squid fixtures/ fixtures/
RUN uv sync --locked --all-extras

CMD ["uv", "run", "pytest", "-q"]


# The server, last so it's what a plain `docker build` makes: the app and its api
# extra, no dev tools and no tests.
FROM base AS serve

COPY --chown=squid pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --extra api --no-dev --no-install-project

COPY --chown=squid src/ src/
RUN uv sync --locked --extra api --no-dev

EXPOSE 8000

# The image has no curl; the standard library asks instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --start-interval=1s \
    CMD ["python", "-c", "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# One process; all PDF work runs in its worker pool. Keep-alive outlasts Caddy's two idle
# minutes, so the proxy never reuses a connection the app has just closed.
CMD ["uvicorn", "squidpdf.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "130"]
