"""Every log line has one shape, its event says how it went, and no field carries a file."""

from __future__ import annotations

from typing import Any

import pytest

from squidpdf.core import ERROR, LogController, LogEvent
from tests.conftest import log_lines
from tests.helpers import assert_equal

# Named under the package, as every module is, so the server's handler writes it.
_log = LogController.for_module("squidpdf.tests.core.test_logs")


def test_configuring_twice_still_writes_each_line_once(capsys):
    """Each test's `create_app()` configures again; a second handler would double every line."""
    LogController.configure()
    LogController.configure()
    _log.write(LogEvent.REQUEST_DONE, status=200)

    lines = log_lines(capsys.readouterr().out)
    assert_equal(len(lines), 1, "lines written for one event")


def test_an_event_is_written_at_its_own_level_unless_the_call_says_another(capsys):
    """Its outcome and usual level are the event's; a server error's request says it's worse."""
    LogController.configure()
    _log.write(LogEvent.GOOGLE_FETCH_LATE, timeout_s=2.5)
    _log.write(LogEvent.SWEEP_FAILED, OSError("the disk said no"))
    _log.write(LogEvent.REQUEST_DONE, level=ERROR, status=500)

    lines = log_lines(capsys.readouterr().out)
    said = [tuple(line.split()[1:3]) for line in lines]
    expected = [("WARN", "skipped"), ("ERROR", "failed"), ("ERROR", "noted")]
    assert_equal(said, expected, "each line's level and outcome")


def test_only_a_failed_event_takes_what_failed():
    """Its traceback follows the line; a line that says nothing failed has none to give."""
    with pytest.raises(TypeError):
        _log.write(LogEvent.SWEEP_FAILED)
    with pytest.raises(TypeError):
        _log.write(LogEvent.REQUEST_DONE, OSError("not a failure of the request"))


def test_every_event_is_written_as_its_own_name():
    """So a grep for the word finds the member, and two events never share a word."""
    words = {event.name: event.value.written for event in LogEvent}
    assert_equal(words, {name: name.lower() for name in words}, "each event's word")


def test_a_plain_string_field_is_refused():
    """A str may be a file's words: a font's name read from the PDF, a span's text."""
    read_from_a_file: Any = "Inter"  # past the type checkers, which refuse it themselves
    with pytest.raises(TypeError):
        _log.write(LogEvent.GOOGLE_CACHE_MISSED, font=read_from_a_file)
