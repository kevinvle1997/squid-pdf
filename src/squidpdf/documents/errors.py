"""What can go wrong with a document: the upload, a page, or its expiry."""

from __future__ import annotations

from squidpdf.core import NotFound, Problem, words


class NotAPdf(Problem):
    """The upload doesn't start like a PDF."""

    type = "not_a_pdf"
    status = 415
    sentence = words.NOT_A_PDF


class TooLarge(Problem):
    """The upload is over the size limit."""

    type = "too_large"
    status = 413
    sentence = words.TOO_LARGE

    def __init__(self, mb: int) -> None:
        """Name the limit it went over."""
        super().__init__(mb=mb)


class TooManyPages(Problem):
    """The PDF has more pages than the server works on; checked before any other work."""

    type = "too_many_pages"
    status = 422
    sentence = words.TOO_MANY_PAGES

    def __init__(self, pages: int) -> None:
        """Name the limit it went over."""
        super().__init__(pages=pages)


class NoSuchPage(Problem):
    """A page the document doesn't have. Not 404, which tells the browser to re-upload."""

    type = "no_such_page"
    status = 422
    sentence = words.NO_SUCH_PAGE


class Gone(NotFound):
    """Deleted while a request was using it: it expired mid-way, so the browser re-uploads."""
