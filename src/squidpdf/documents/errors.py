from __future__ import annotations

from squidpdf.core import NotFound, Problem


class NotAPdf(Problem):
    type = "not_a_pdf"
    status = 415


class FontMismatch(Problem):
    type = "font_mismatch"
    status = 422


class NotAFont(Problem):
    type = "not_a_font"
    status = 422


class FontUnchecked(Problem):
    type = "font_unchecked"
    status = 422


class TooManyFonts(Problem):
    type = "too_many_fonts"
    status = 422

    def __init__(self, count: int) -> None:
        super().__init__(count=count)


class NoSuchFont(Problem):
    type = "no_such_font"
    status = 422


class NotSentAsPdf(Problem):
    type = "not_sent_as_pdf"
    status = 415


class TooLarge(Problem):
    type = "too_large"
    status = 413

    def __init__(self, mb: int) -> None:
        super().__init__(mb=mb)


class TooManyPages(Problem):
    type = "too_many_pages"
    status = 422

    def __init__(self, pages: int) -> None:
        super().__init__(pages=pages)


class ServerFull(Problem):
    type = "server_full"
    status = 503


class NoSuchPage(Problem):
    """Not 404, which tells the browser to re-upload."""

    type = "no_such_page"
    status = 422


def is_page(page: int, page_count: int) -> bool:
    return 0 <= page < page_count


class Gone(NotFound): ...
