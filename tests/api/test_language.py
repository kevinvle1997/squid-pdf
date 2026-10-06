"""Every sentence is said in the language the browser asks for, and kept apart by it."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import words
from tests.api.conftest import upload
from tests.conftest import PSEUDO, pseudo_sentence
from tests.helpers import assert_equal, assert_in, assert_problem


def _in(language: str) -> dict[str, str]:
    """The header a browser asks for `language` with."""
    return {"accept-language": language}


@pytest.mark.parametrize(
    ("accept_language", "chosen"),
    [
        (None, "en"),
        (f"{PSEUDO}-XX", PSEUDO),
        (f"fr, {PSEUDO};q=0.9, en;q=0.8", PSEUDO),
        (f"{PSEUDO};q=0", "en"),
        (f"{PSEUDO}-XX;q=0.9, {PSEUDO};q=0", "en"),
    ],
    ids=[
        "no header",
        "one we have, more exactly",
        "the best we have, by weight",
        "one refused",
        "one refused, though a longer form of it is wanted",
    ],
)
def test_the_language_answered_in_is_the_most_wanted_one_we_have(
    pseudo, mine, doc, accept_language, chosen
):
    headers = {} if accept_language is None else _in(accept_language)
    response = mine.get(f"/api/documents/{doc['id']}", headers=headers)
    assert_equal(
        response.headers["content-language"], chosen, f"the language for {accept_language!r}"
    )


def test_a_document_s_sentences_come_in_the_language_asked_for(pseudo, mine, doc):
    """Uploaded in English, read in another: the analysis keeps codes, not sentences."""
    response = mine.get(f"/api/documents/{doc['id']}", headers=_in(PSEUDO))
    assert_equal(response.headers["content-language"], PSEUDO, "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")
    copy = response.json()["copy"]
    assert_equal(
        copy["missing"], pseudo_sentence(words.sentence("missing")), "a sentence to fill"
    )
    empty = copy["empty"]
    assert_equal(empty, pseudo_sentence(words.sentence("empty")), "a sentence said as typed")
    shrink = copy["options"]["shrink"]["label"]
    assert_equal(shrink, pseudo_sentence(words.sentence("shrink_label")), "a way out's name")
    turned = copy["approximate"]["turned_text"]
    assert_equal(turned, pseudo_sentence(words.sentence("turned_text")), "why text won't match")
    # Times is only named in the sample: why its own copy can't be used, said and unsaid.
    times = next(font for font in response.json()["fonts"] if font["name"] == "Times-Roman")
    said = (times["why"], times["why_code"], times["why_params"])
    expected: tuple[str, str, dict] = (
        pseudo_sentence(words.sentence("font_not_in_file")),
        "font_not_in_file",
        {},
    )
    assert_equal(said, expected, "why Times stands in")


def test_one_language_s_etag_never_answers_for_another(pseudo, mine, doc):
    url = f"/api/documents/{doc['id']}"
    english = mine.get(url)
    assert_equal(english.headers["cache-control"], "private, no-cache", "document caching")
    asked_again = {"if-none-match": english.headers["etag"]}
    same = mine.get(url, headers=asked_again)
    assert_equal(same.status_code, 304, "the same language, unchanged")
    assert_in("Accept-Language", same.headers["vary"], "what the 304 varies by")
    other = mine.get(url, headers={**asked_again, **_in(PSEUDO)})
    assert_equal(other.status_code, 200, "another language, with English's ETag")
    missing = other.json()["copy"]["missing"]
    assert_equal(
        missing, pseudo_sentence(words.sentence("missing")), "the body it answered with"
    )


def test_a_document_with_no_text_says_so_with_its_code(pseudo, mine):
    blank = pymupdf.open()
    blank.new_page()
    notices = upload(mine, blank.tobytes()).json()["notices"]
    no_text = {
        "type": "no_text",
        "code": "no_text",
        "params": {},
        "detail": words.sentence("no_text"),
    }
    assert_equal(notices, [no_text], "the notice in English")
    response = mine.post(
        "/api/documents",
        content=blank.tobytes(),
        headers={"content-type": "application/pdf", **_in(PSEUDO)},
    )
    detail = response.json()["notices"][0]["detail"]
    assert_equal(
        detail, pseudo_sentence(words.sentence("no_text")), "the notice in the pseudo-language"
    )


def test_a_problem_is_said_in_the_language_asked_for_with_its_code(pseudo, mine):
    response = mine.post(
        "/api/documents",
        content=b"Dear Sir, please find attached.",
        headers={"content-type": "application/pdf", **_in(PSEUDO)},
    )
    assert_problem(response, "not_a_pdf", 415)
    body = response.json()
    expected: tuple[str, str, dict] = (
        pseudo_sentence(words.sentence("not_a_pdf")),
        "not_a_pdf",
        {},
    )
    assert_equal((body["detail"], body["code"], body["params"]), expected, "what it says")
    assert_equal(response.headers["content-language"], PSEUDO, "the language it's in")
    assert_in("Accept-Language", response.headers["vary"], "what the answer varies by")


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
    expected: tuple[str, str, dict] = (
        pseudo_sentence(words.sentence("no_span")),
        "no_span",
        {},
    )
    assert_equal((skipped["detail"], skipped["code"], skipped["params"]), expected, "the skip")
    fit = rendered["fits"][span["id"]]
    too_long = pseudo_sentence(words.sentence("too_long")).format(
        delta_pt=f"{fit['delta_pt']:.1f}"
    )
    assert_equal(fit["message"], too_long, "the fit's message")
    parts = [{"code": "too_long", "params": {"delta_pt": fit["delta_pt"]}}]
    assert_equal(fit["message_parts"], parts, "the fit's message, unsaid")
