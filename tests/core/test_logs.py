"""Every log line has one shape, its level is its caller's, and no field carries a file."""

from __future__ import annotations

from typing import Any

import pytest

from squidpdf.core import ERROR, INFO, WARN, LogController, LogEvent
from tests.conftest import log_lines
from tests.helpers import assert_equal

# Named under the package, as every module is, so the server's handler writes it.
_log = LogController.for_module("squidpdf.tests.core.test_logs")


def test_configuring_twice_still_writes_each_line_once(capsys):
    """Each test's `create_app()` configures again; a second handler would double every line."""
    LogController.configure()
    LogController.configure()
    _log.noted(INFO, LogEvent.REQUEST_DONE, status=200)

    lines = log_lines(capsys.readouterr().out)
    assert_equal(len(lines), 1, "lines written for one noted")


@pytest.mark.parametrize("level", [INFO, WARN, ERROR])
def test_every_outcome_takes_every_level(capsys, level):
    """A skip can be routine or worth a look: the caller says which, and a grep finds both."""
    LogController.configure()
    _log.noted(level, LogEvent.REQUEST_DONE, status=200)
    _log.skipped(level, LogEvent.GOOGLE_FETCH_LATE, timeout_s=2.5)
    _log.failed(level, LogEvent.SWEEP_FAILED, OSError("the disk said no"))

    lines = log_lines(capsys.readouterr().out)
    levels = [line.split()[1] for line in lines]
    assert_equal(levels, [level.name] * 3, "each outcome's level")


def test_a_plain_string_field_is_refused():
    """A str may be a file's words: a font's name read from the PDF, a span's text."""
    read_from_a_file: Any = "Inter"  # past the type checkers, which refuse it themselves
    with pytest.raises(TypeError):
        _log.noted(INFO, LogEvent.GOOGLE_CACHE_MISSED, font=read_from_a_file)
