"""What can go wrong with a document: the upload, a page, or its expiry."""

from __future__ import annotations

from squidpdf.core import NotFound, Problem


class NotAPdf(Problem):
    """The upload doesn't start like a PDF."""

    type = "not_a_pdf"
    status = 415


class TooLarge(Problem):
    """The upload is over the size limit."""

    type = "too_large"
    status = 413

    def __init__(self, mb: int) -> None:
        """Name the limit it went over."""
        super().__init__(mb=mb)


class TooManyPages(Problem):
    """The PDF has more pages than the server works on; checked before any other work."""

    type = "too_many_pages"
    status = 422

    def __init__(self, pages: int) -> None:
        """Name the limit it went over."""
        super().__init__(pages=pages)


class ServerFull(Problem):
    """Too little disk is left to write on, found before an upload or by a write.

    Even a read can raise it, as it writes the analysis under a new build.
    """

    type = "server_full"
    status = 503


class NoSuchPage(Problem):
    """A page the document doesn't have. Not 404, which tells the browser to re-upload."""

    type = "no_such_page"
    status = 422


class Gone(NotFound):
    """Deleted while a request was using it: it expired mid-way, so the browser re-uploads."""
