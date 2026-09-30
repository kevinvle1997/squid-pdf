"""A Problem says the same thing wherever it's raised, a worker included."""

from __future__ import annotations

import pickle
import subprocess
import sys
from functools import partial

import pytest

from squidpdf.core import ErrorController, InvalidRequest, NotFound, Problem, result_of, words
from tests.helpers import assert_equal, assert_in, assert_true


class _Sized(Problem):
    """A problem whose own arguments differ from the base's, as an upload's size limit's do."""

    type = "too_large"

    def __init__(self, mb: int) -> None:
        super().__init__(mb=mb)


def test_a_problem_survives_the_trip_from_a_worker():
    """The API tests' refusals take no arguments; one that does must still unpickle."""
    back = pickle.loads(pickle.dumps(_Sized(100)))
    assert_true(type(back) is _Sized, f"came back as {type(back).__name__}")
    said = words.sentence("too_large").format(mb=100)
    assert_equal(back.detail, said, "the sentence after a pickle round trip")


def _not_found(exc: Exception) -> Problem:
    """A row's Problem, for a test: not found."""
    return NotFound(debug=str(exc))


def _invalid(exc: Exception) -> Problem:
    """A row's Problem, for a test: a bad request."""
    return InvalidRequest(debug=str(exc))


def test_the_error_controller_asks_its_rows_in_order_and_a_layer_above_adds_its_own():
    core = ErrorController(((KeyError, _not_found),))
    above = core.with_rows((LookupError, _invalid), (KeyError, _invalid))
    said = [
        (core.problem_of(KeyError("k")).type, "core's own row"),
        (above.problem_of(KeyError("k")).type, "core's row, asked before the ones added"),
        (above.problem_of(IndexError("i")).type, "a row added above"),
        (core.problem_of(IndexError("i")).type, "core, unchanged by what was added above"),
        (core.problem_of(NotFound()).type, "a Problem, as it was raised"),
    ]
    expected = [
        ("not_found", "core's own row"),
        ("not_found", "core's row, asked before the ones added"),
        ("invalid_request", "a row added above"),
        ("server_error", "core, unchanged by what was added above"),
        ("not_found", "a Problem, as it was raised"),
    ]
    assert_equal(said, expected, "what each failure means")


def test_a_failure_no_row_claims_goes_up_as_it_is_and_the_top_calls_it_a_server_error():
    """A worker sends it back as itself: a layer above may claim it, or log it as a bug."""
    controller = ErrorController(((KeyError, _not_found),))
    with pytest.raises(ZeroDivisionError):
        controller.result_of(partial(divmod, 1, 0))
    problem = controller.problem_of(ZeroDivisionError("a bug"))
    said = (problem.type, problem.status, problem.debug)
    assert_equal(said, ("server_error", 500, "ZeroDivisionError: a bug"), "an unknown failure")


def test_a_file_the_machine_cannot_write_is_a_server_error_not_too_heavy(engine, tmp_path):
    """A document deleted mid-export once answered "needs more memory" (review 1.8)."""
    with pytest.raises(Problem) as caught:
        result_of(partial(engine.save, str(tmp_path / "deleted" / "out.pdf")))
    said = (caught.value.type, caught.value.status)
    assert_equal(said, ("server_error", 500), "a file the machine couldn't write")


# Run apart, since it caps its own memory: the page image asks for far more than it's left.
_OUT_OF_MEMORY = """
import resource, sys
from functools import partial
from squidpdf.core import Problem, open_pdf, result_of
engine = open_pdf(sys.argv[1])
image = partial(engine.page_image, 0, 18.0)  # about 470 MB of pixels
with open("/proc/self/statm") as statm:
    in_use = int(statm.read().split()[0]) * resource.getpagesize()
cap = in_use + 200_000_000
resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
try:
    result_of(image)
except Problem as problem:
    print(problem.type, problem.debug)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="the memory cap is Linux only")
def test_mupdf_running_out_of_memory_is_too_heavy(pdf):
    """MuPDF raises the same error for a file it couldn't open: its words tell them apart."""
    run = [sys.executable, "-c", _OUT_OF_MEMORY, pdf]
    said = subprocess.run(run, capture_output=True, text=True, check=True).stdout
    assert_in("too_heavy FzErrorSystem", said, "a page image past the memory cap")
