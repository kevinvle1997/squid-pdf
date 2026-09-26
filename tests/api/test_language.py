"""Every sentence is said in the language the browser asks for, and kept apart by it."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.api.language import negotiate
from squidpdf.core import words
from tests.api.conftest import upload
from tests.conftest import PSEUDO, pseudo_sentence
from tests.helpers import assert_equal, assert_in, assert_problem


@pytest.fixture
def mine(browser):
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine, pdf_bytes) -> dict:
    """The sample PDF as uploaded by `mine`, in English."""
    return upload(mine, pdf_bytes).json()


def _in(language: str) -> dict[str, str]:
    """The header a browser asks for `language` with."""
    return {"accept-language": language}


@pytest.mark.parametrize(
    ("accept_language", "chosen"),
    [
        (None, "en"),
        ("", "en"),
        ("fr-FR, fr;q=0.9", "en"),
        (PSEUDO, PSEUDO),
        (f"{PSEUDO}-XX", PSEUDO),
        (f"fr, {PSEUDO};q=0.9, en;q=0.8", PSEUDO),
        (f"en;q=0.5, {PSEUDO}", PSEUDO),
        (f"en, {PSEUDO}", "en"),
        (f"{PSEUDO};q=0", "en"),
        (f"{PSEUDO};q=nonsense", "en"),
        (f"*, {PSEUDO};q=0.5", "en"),
    ],
    ids=[
        "no header",
        "an empty header",
        "only languages we lack",
        "one we have",
        "one we have, more exactly",
        "the best we have, by weight",
        "a heavier one later in the list",
        "the first of two equally wanted",
        "one refused",
        "a weight that can't be read",
        "any language",
    ],
)
def test_the_language_answered_in_is_the_most_wanted_one_we_have(
    pseudo, accept_language, chosen
):
    assert_equal(negotiate(accept_language), chosen, f"the language for {accept_language!r}")


def test_a_document_is_answered_in_english_by_default_and_says_so(mine, pdf_bytes):
    response = upload(mine, pdf_bytes)
    assert_equal(response.headers["content-language"], "en", "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")
    assert_equal(response.json()["copy"]["missing"], words.MISSING, "the English sentence")


def test_a_document_s_sentences_come_in_the_language_asked_for(pseudo, mine, doc):
    response = mine.get(f"/api/documents/{doc['id']}", headers=_in(PSEUDO))
    assert_equal(response.headers["content-language"], PSEUDO, "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")
    copy = response.json()["copy"]
    assert_equal(copy["missing"], pseudo_sentence(words.MISSING), "a sentence to fill")
    shrink = copy["options"]["shrink"]["label"]
    assert_equal(shrink, pseudo_sentence(words.SHRINK_LABEL), "a way out's name")


def test_why_a_font_stands_in_is_said_in_the_language_asked_for(pseudo, mine, doc):
    def times(document: dict) -> dict:
        """What the document says about Times, which the sample only names."""
        return next(font for font in document["fonts"] if font["name"] == "Times-Roman")

    english = times(doc)
    expected: tuple[str, str, dict] = (words.FONT_NOT_IN_FILE, "font_not_in_file", {})
    said = (english["why"], english["why_code"], english["why_params"])
    assert_equal(said, expected, "why, in English and unsaid")
    other = times(mine.get(f"/api/documents/{doc['id']}", headers=_in(PSEUDO)).json())
    assert_equal(other["why"], pseudo_sentence(words.FONT_NOT_IN_FILE), "why, in pseudo")
    exact = next(font for font in doc["fonts"] if font["why"] is None)
    assert_equal((exact["why_code"], exact["why_params"]), (None, {}), "a font that's used")


def test_one_language_s_etag_never_answers_for_another(pseudo, mine, doc):
    url = f"/api/documents/{doc['id']}"
    english = mine.get(url)
    asked_again = {"if-none-match": english.headers["etag"]}
    same = mine.get(url, headers=asked_again)
    assert_equal(same.status_code, 304, "the same language, unchanged")
    assert_in("Accept-Language", same.headers["vary"], "what the 304 varies by")
    other = mine.get(url, headers={**asked_again, **_in(PSEUDO)})
    assert_equal(other.status_code, 200, "another language, with English's ETag")
    missing = other.json()["copy"]["missing"]
    assert_equal(missing, pseudo_sentence(words.MISSING), "the body it answered with")


def test_a_document_with_no_text_says_so_with_its_code(pseudo, mine):
    blank = pymupdf.open()
    blank.new_page()
    notices = upload(mine, blank.tobytes()).json()["notices"]
    no_text = {"type": "no_text", "code": "no_text", "params": {}, "detail": words.NO_TEXT}
    assert_equal(notices, [no_text], "the notice in English")
    response = mine.post(
        "/api/documents",
        content=blank.tobytes(),
        headers={"content-type": "application/pdf", **_in(PSEUDO)},
    )
    detail = response.json()["notices"][0]["detail"]
    assert_equal(detail, pseudo_sentence(words.NO_TEXT), "the notice in the pseudo-language")


def test_a_problem_is_said_in_the_language_asked_for_with_its_code(pseudo, mine):
    response = mine.post(
        "/api/documents", content=b"Dear Sir, please find attached.", headers=_in(PSEUDO)
    )
    assert_problem(response, "not_a_pdf", 415)
    body = response.json()
    expected: tuple[str, str, dict] = (pseudo_sentence(words.NOT_A_PDF), "not_a_pdf", {})
    assert_equal((body["detail"], body["code"], body["params"]), expected, "what it says")
    assert_equal(response.headers["content-language"], PSEUDO, "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")


def test_a_problem_s_facts_come_with_it(mine, doc):
    edits = [{"kind": "redact", "span_id": "nosuchspan00"}]
    body = {"edits": edits, "scale": 2, "regions": [{"page": 0}]}
    response = mine.post(f"/api/documents/{doc['id']}/render", json=body)
    assert_problem(response, "bad_reference", 422)
    got = response.json()
    expected = ("bad_reference", {"span_id": "nosuchspan00"})
    assert_equal((got["code"], got["params"]), expected, "the problem, unsaid")


def test_render_says_what_went_wrong_in_the_language_asked_for(pseudo, mine, doc):
    span = next(s for s in doc["spans"] if s["text"].startswith("Made"))
    edits = [
        {"kind": "replace", "span_id": "nosuchspan00", "text": "x"},
        {"kind": "replace", "span_id": span["id"], "text": span["text"] + "!!"},
    ]
    body = {"edits": edits, "scale": 2, "regions": []}
    response = mine.post(f"/api/documents/{doc['id']}/render", json=body, headers=_in(PSEUDO))
    assert_equal(response.headers["content-language"], PSEUDO, "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")
    rendered = response.json()

    [skipped] = rendered["skipped"]
    expected: tuple[str, str, dict] = (pseudo_sentence(words.NO_SPAN), "no_span", {})
    assert_equal((skipped["detail"], skipped["code"], skipped["params"]), expected, "the skip")
    fit = rendered["fits"][span["id"]]
    too_long = pseudo_sentence(words.TOO_LONG).format(delta_pt=f"{fit['delta_pt']:.1f}")
    assert_equal(fit["message"], too_long, "the fit's message")
    parts = [{"code": "too_long", "params": {"delta_pt": fit["delta_pt"]}}]
    assert_equal(fit["message_parts"], parts, "the fit's message, unsaid")
