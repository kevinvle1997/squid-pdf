FROM debian:bookworm-slim AS base
# CI reads its uv version from this line (.github/actions/uv).
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Installed as root, so the user that reads uploads can't change its own interpreter.
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN --mount=type=bind,source=.python-version,target=/tmp/.python-version \
    uv python install --no-bin "$(cat /tmp/.python-version)"
ENV UV_PYTHON_DOWNLOADS=never

# Uploaded PDFs are hostile input, so nothing runs as root. 999 is pinned so a rebuild keeps volumes writable.
RUN groupadd --system --gid 999 squid \
    && useradd --system --uid 999 --gid squid --create-home squid \
    && mkdir /app /data /accounts && chown squid /app /data /accounts
USER squid
WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    SQUIDPDF_DATA=/data


FROM base AS test

COPY --chown=squid pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --all-extras --no-install-project

COPY --chown=squid src/ src/
COPY --chown=squid tests/ tests/
COPY --chown=squid fixtures/ fixtures/
RUN uv sync --locked --all-extras

CMD ["uv", "run", "pytest", "-q"]


FROM base AS serve

# Compiled here, since on compose's read-only root every worker would compile at start.
ENV UV_COMPILE_BYTECODE=1

COPY --chown=squid pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --extra api --no-dev --no-install-project

COPY --chown=squid src/ src/
RUN uv sync --locked --extra api --no-dev && python -m compileall -q src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --start-interval=1s \
    CMD ["python", "-c", "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# Keep-alive outlasts Caddy's two idle minutes, so it never reuses a connection the app just closed.
CMD ["uvicorn", "squidpdf.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "130", \
     "--no-access-log"]
