"""Serve the API for the browser's end to end tests: every spec sends from one address."""

from __future__ import annotations

import sys

import uvicorn

from squidpdf.api import constants

_SUITE_AT_ONCE = 1_000
_PER_ADDRESS = ("UPLOADS_PER_MINUTE", "UPLOADS_UNDER_WAY", "JOBS_AT_WORK")


def main() -> None:
    for limit in _PER_ADDRESS:
        setattr(constants, limit, _SUITE_AT_ONCE)
    uvicorn.run("squidpdf.api.app:create_app", factory=True, port=int(sys.argv[1]))


# Only when run: the API's workers start by importing this module again, and mustn't serve too.
if __name__ == "__main__":
    main()
