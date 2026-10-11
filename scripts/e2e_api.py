"""Serve the API for the browser's end to end tests, as `react/playwright.config.ts` starts it.

    uv run python scripts/e2e_api.py 8765

The app as deployed, but for its per-address limits: every spec sends from one
address, so the run would be refused past them, as the API's own tests would
(`tests/api/conftest.py`, which raises the same three).
"""

from __future__ import annotations

import sys

import uvicorn

from squidpdf.api import constants

_SUITE_AT_ONCE = 1_000  # past what any run asks for, from its one address
_PER_ADDRESS = ("UPLOADS_PER_MINUTE", "UPLOADS_UNDER_WAY", "JOBS_AT_WORK")


def main() -> None:
    """Raise the limits, then serve: the server's process is the one that counts requests."""
    # The per-address limits read these when each request comes in. By name, as a test's
    # monkeypatch sets them: each is typed as the value it's written with.
    for limit in _PER_ADDRESS:
        setattr(constants, limit, _SUITE_AT_ONCE)
    uvicorn.run("squidpdf.api.app:create_app", factory=True, port=int(sys.argv[1]))


# Only when run: the API's workers start by importing this module again, and mustn't serve too.
if __name__ == "__main__":
    main()
