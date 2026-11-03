"""Signing in by a link sent by email; on a server that keeps no accounts, no signing in."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.accounts.deployment import Accounts, AccountsUnset
from squidpdf.accounts.mail import Letter, SmtpMailer
from squidpdf.accounts.signed import signed
from squidpdf.accounts.users import open_users
from squidpdf.api import constants as limits
from squidpdf.api.app import create_app
from tests.api.conftest import BASE_URL, browser_on, upload
from tests.helpers import assert_equal, assert_in, assert_problem, assert_true

_SECRET = b"a test's secret, long enough to sign with"
_SITE = "https://pdf.example.com"
_EMAIL = "reader@example.com"


@dataclass(slots=True)
class _NotedMail:
    """A mail server that only notes each letter it's given."""

    letters: list[Letter] = field(default_factory=list)

    def send(self, letter: Letter) -> None:
        self.letters.append(letter)


def _keeping_accounts(app: FastAPI, tmp_path, monkeypatch, *, invite_only: bool) -> _NotedMail:
    """The app keeping accounts in a fresh table, its letters noted rather than sent."""
    noted = _NotedMail()
    accounts = Accounts(
        open_users(tmp_path / "users.sqlite3"),
        noted,
        secret=_SECRET,
        site=_SITE,
        invite_only=invite_only,
    )
    monkeypatch.setattr(app.state, "accounts", accounts)
    return noted


@pytest.fixture
def mail(app: FastAPI, tmp_path, monkeypatch) -> _NotedMail:
    """The app keeping accounts anyone may sign in to, its letters noted here."""
    return _keeping_accounts(app, tmp_path, monkeypatch, invite_only=False)


@pytest.fixture
def invited_mail(app: FastAPI, tmp_path, monkeypatch) -> _NotedMail:
    """The app open to invited people only, its letters noted here."""
    return _keeping_accounts(app, tmp_path, monkeypatch, invite_only=True)


def _token_in(letter: Letter) -> str:
    """The token the link in `letter` carries, as the browser reads it from the link."""
    link = next(line for line in letter.body.splitlines() if line.startswith(_SITE))
    assert_true(
        link.startswith(f"{_SITE}/sign-in#"), f"the link opens the sign-in page: {link}"
    )
    return link.partition("#")[2]


def test_a_link_by_email_signs_a_browser_in_and_makes_its_account(
    mail, browser: Callable[[], TestClient]
):
    reader, other = browser(), browser()
    assert_equal(
        reader.get("/api/account").json(),
        {"offered": True, "invite_only": False, "signed_in": None},
        "before",
    )
    sent = reader.post("/api/account/link", json={"email": f"  {_EMAIL.upper()} "})
    assert_equal(sent.status_code, 202, "the link is on its way")
    assert_in(_EMAIL, sent.json()["sentence"], "the sentence names where it went")
    [letter] = mail.letters
    assert_equal(letter.to, _EMAIL, "the address, trimmed and in lower case")
    signed_in = reader.post("/api/account/sign-in", json={"token": _token_in(letter)})
    expected = {
        "offered": True,
        "invite_only": False,
        "signed_in": {"email": _EMAIL, "is_unlimited": False},
    }
    assert_equal(signed_in.json(), expected, "signed in")
    assert_in("HttpOnly", signed_in.headers["set-cookie"], "script never reads the cookie")
    assert_in("SameSite=Strict", signed_in.headers["set-cookie"], "no other site sends it")
    assert_equal(reader.get("/api/account").json(), expected, "still signed in")
    assert_equal(other.get("/api/account").json()["signed_in"], None, "another browser isn't")
    signed_out = reader.post("/api/account/sign-out")
    assert_equal(signed_out.json()["signed_in"], None, "signed out")
    assert_equal(reader.get("/api/account").json()["signed_in"], None, "and stays out")


def test_a_link_past_its_time_altered_or_not_a_link_signs_no_one_in(
    mail, browser: Callable[[], TestClient]
):
    reader = browser()
    past = signed(_EMAIL, purpose="link", until=1, secret=_SECRET)
    # A signed-in cookie is signed with the same secret, for another purpose.
    cookie = signed(_EMAIL, purpose="signed_in", until=2**40, secret=_SECRET)
    reader.post("/api/account/link", json={"email": _EMAIL})
    whole = _token_in(mail.letters[0])
    altered = whole[:-2] + ("AA" if not whole.endswith("AA") else "BB")
    for token in (past, cookie, altered, "not a token"):
        refused = reader.post("/api/account/sign-in", json={"token": token})
        assert_problem(refused, "link_expired", 400)
    assert_equal(reader.get("/api/account").json()["signed_in"], None, "nobody signed in")


def test_what_cannot_be_an_email_address_is_refused_before_anything_is_sent(
    mail, browser: Callable[[], TestClient]
):
    reader = browser()
    for typed in ("reader", "reader@localhost", "two words@example.com", "@example.com"):
        refused = reader.post("/api/account/link", json={"email": typed})
        assert_problem(refused, "not_an_email", 422)
    assert_equal(mail.letters, [], "letters sent")


def test_a_friend_invited_without_a_limit_signs_in_as_one(
    mail, app: FastAPI, browser: Callable[[], TestClient]
):
    app.state.accounts.users.invite(_EMAIL, now=0, is_unlimited=True)
    reader = browser()
    reader.post("/api/account/link", json={"email": _EMAIL})
    signed_in = reader.post("/api/account/sign-in", json={"token": _token_in(mail.letters[0])})
    assert_equal(signed_in.json()["signed_in"]["is_unlimited"], True, "no document limit")


def test_a_mail_server_that_cannot_be_reached_says_the_link_was_not_sent(
    app: FastAPI, tmp_path, monkeypatch, browser: Callable[[], TestClient]
):
    # Nothing listens on port 1 of this machine: the connection is refused at once.
    unreachable = SmtpMailer(
        "127.0.0.1", 1, security="starttls", user="u", password="p", sender="from@example.com"
    )
    accounts = Accounts(
        open_users(tmp_path / "users.sqlite3"),
        unreachable,
        secret=_SECRET,
        site=_SITE,
        invite_only=False,
    )
    monkeypatch.setattr(app.state, "accounts", accounts)
    refused = browser().post("/api/account/link", json={"email": _EMAIL})
    assert_problem(refused, "mail_not_sent", 503)


def test_a_server_with_no_database_offers_no_accounts(browser: Callable[[], TestClient]):
    reader = browser()
    assert_equal(
        reader.get("/api/account").json(),
        {"offered": False, "invite_only": False, "signed_in": None},
        "state",
    )
    assert_problem(reader.post("/api/account/link", json={"email": _EMAIL}), "no_accounts", 404)
    assert_problem(reader.post("/api/account/sign-in", json={"token": "x"}), "no_accounts", 404)


def test_an_address_past_its_links_a_minute_is_told_to_wait(tmp_path, monkeypatch):
    """Its own app: every other test asks from the one test address."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(limits, "LINKS_PER_MINUTE", 1)
    noted = _NotedMail()
    app = create_app()
    app.state.accounts = Accounts(
        open_users(tmp_path / "users.sqlite3"),
        noted,
        secret=_SECRET,
        site=_SITE,
        invite_only=False,
    )
    with TestClient(app) as server:  # runs the lifespan, which makes the counts
        reader = browser_on(server, client=("192.0.2.1", 1))
        neighbour = browser_on(server, client=("198.51.100.7", 1))
        assert_equal(
            reader.post("/api/account/link", json={"email": _EMAIL}).status_code, 202, "1"
        )
        again = reader.post("/api/account/link", json={"email": _EMAIL})
        assert_problem(again, "rate_limited", 429)
        assert_equal(
            neighbour.post("/api/account/link", json={"email": _EMAIL}).status_code, 202, "2"
        )
    assert_equal(len(noted.letters), 2, "letters sent")


@pytest.mark.parametrize(
    "missing",
    ["SQUIDPDF_SECRET", "SQUIDPDF_SITE", "SQUIDPDF_SMTP_HOST", "SQUIDPDF_MAIL_FROM"],
)
def test_a_database_named_without_what_accounts_need_stops_the_start(
    tmp_path, monkeypatch, missing
):
    """Said as the server starts, not on the first sign-in, when someone is waiting."""
    whole = {
        "SQUIDPDF_DATABASE": str(tmp_path / "users.sqlite3"),
        "SQUIDPDF_SECRET": _SECRET.decode(),
        "SQUIDPDF_SITE": _SITE,
        "SQUIDPDF_SMTP_HOST": "mail.example.com",
        "SQUIDPDF_SMTP_PORT": "587",
        "SQUIDPDF_SMTP_SECURITY": "starttls",
        "SQUIDPDF_SMTP_USER": "squid",
        "SQUIDPDF_SMTP_PASSWORD": "secret",
        "SQUIDPDF_MAIL_FROM": "sign-in@example.com",
    }
    for name, value in whole.items():
        monkeypatch.setenv(name, value)
    assert_equal(
        TestClient(create_app(), base_url=BASE_URL).get("/api/account").json()["offered"],
        True,
        "with everything set",
    )
    monkeypatch.delenv(missing)
    with pytest.raises(AccountsUnset, match=missing):
        create_app()


def test_invite_only_a_stranger_opens_nothing_until_invited_and_signed_in(
    invited_mail, app: FastAPI, browser: Callable[[], TestClient], pdf_bytes
):
    reader = browser()
    state = reader.get("/api/account").json()
    assert_equal(state["invite_only"], True, "the landing knows to ask for a sign-in")
    assert_problem(upload(reader, pdf_bytes), "invited_only", 401)
    assert_problem(reader.get("/api/fonts"), "invited_only", 401)
    # What the landing needs before anyone signs in.
    for open_path in ("/api/health", "/api/terms", "/api/account"):
        assert_equal(reader.get(open_path).status_code, 200, open_path)
    app.state.accounts.users.invite(_EMAIL, now=0, is_unlimited=False)
    reader.post("/api/account/link", json={"email": _EMAIL})
    reader.post("/api/account/sign-in", json={"token": _token_in(invited_mail.letters[0])})
    assert_equal(upload(reader, pdf_bytes).status_code, 201, "signed in, the upload is read")
    reader.post("/api/account/sign-out")
    assert_problem(upload(reader, pdf_bytes), "invited_only", 401)


def test_invite_only_an_address_no_one_invited_gets_no_link_and_hears_what_the_invited_do(
    invited_mail, app: FastAPI, browser: Callable[[], TestClient]
):
    """Whether someone is invited is theirs and the server's to know, no one else's."""
    app.state.accounts.users.invite(_EMAIL, now=0, is_unlimited=False)
    stranger = browser().post("/api/account/link", json={"email": "stranger@example.com"})
    invited = browser().post("/api/account/link", json={"email": _EMAIL})
    for sent in (stranger, invited):
        assert_equal(sent.status_code, 202, "the same reply")
        assert_in("If ", sent.json()["sentence"], "said as a condition")
    assert_equal([letter.to for letter in invited_mail.letters], [_EMAIL], "letters sent")


def test_invite_only_a_link_for_an_address_no_one_invited_signs_no_one_in(
    invited_mail, browser: Callable[[], TestClient]
):
    """As a link sent before the server was invite-only would be."""
    token = signed("stranger@example.com", purpose="link", until=2**40, secret=_SECRET)
    refused = browser().post("/api/account/sign-in", json={"token": token})
    assert_problem(refused, "not_invited", 403)
