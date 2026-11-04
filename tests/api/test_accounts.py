"""Signing in by a link sent by email; on a server that keeps no accounts, no signing in."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.accounts.deployment import Accounts, AccountsUnset, Allowance
from squidpdf.accounts.mail import Letter, Mailer, SmtpMailer
from squidpdf.accounts.metering import AccountMeters
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


def _accounts(
    tmp_path, mailer: Mailer, *, invite_only: bool = False, allowance: Allowance | None = None
) -> Accounts:
    """Accounts kept in a fresh table, their letters sent through `mailer`."""
    return Accounts(
        open_users(tmp_path / "users.sqlite3"),
        mailer,
        secret=_SECRET,
        site=_SITE,
        invite_only=invite_only,
        allowance=allowance,
    )


def _keeping_accounts(
    app: FastAPI,
    tmp_path,
    monkeypatch,
    *,
    invite_only: bool,
    allowance: Allowance | None = None,
) -> _NotedMail:
    """The app keeping accounts in a fresh table, its letters noted rather than sent.

    With an allowance, each upload is metered against it, as the app does when it starts.
    """
    noted = _NotedMail()
    accounts = _accounts(tmp_path, noted, invite_only=invite_only, allowance=allowance)
    monkeypatch.setattr(app.state, "accounts", accounts)
    if allowance is not None:
        monkeypatch.setattr(app.state, "meters", AccountMeters(accounts, allowance))
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
    for token in (past, cookie, altered, "not a token", "a.\u00e9"):
        refused = reader.post("/api/account/sign-in", json={"token": token})
        assert_problem(refused, "link_expired", 400)
    assert_equal(reader.get("/api/account").json()["signed_in"], None, "nobody signed in")
    # A cookie anyone could send, a letter past ASCII in it: read as no one, not a server error.
    reader.cookies.set("__Host-signed-in", "a.\u00e9", domain="testserver")
    assert_equal(reader.get("/api/account").json()["signed_in"], None, "a forged cookie")


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
    monkeypatch.setattr(app.state, "accounts", _accounts(tmp_path, unreachable))
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
    app.state.accounts = _accounts(tmp_path, noted)
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


@pytest.fixture
def metered_mail(app: FastAPI, tmp_path, monkeypatch) -> _NotedMail:
    """The app opening two documents without an account and three a month with one."""
    allowance = Allowance(free=2, monthly=3)
    return _keeping_accounts(app, tmp_path, monkeypatch, invite_only=False, allowance=allowance)


def _files(pdf_bytes: bytes, count: int) -> list[bytes]:
    """`count` different files that each open: the sample, a comment after its end apiece."""
    return [pdf_bytes + f"\n% file {number}\n".encode() for number in range(count)]


def _sign_in(reader: TestClient, mail: _NotedMail, email: str = _EMAIL) -> None:
    """Sign `reader` in as `email`, by the link the server mails there."""
    reader.post("/api/account/link", json={"email": email})
    signed_in = reader.post("/api/account/sign-in", json={"token": _token_in(mail.letters[-1])})
    assert_equal(signed_in.status_code, 200, "signed in")


def test_with_no_account_a_browser_opens_its_free_documents_once_each_then_must_sign_in(
    metered_mail, browser: Callable[[], TestClient], pdf_bytes
):
    reader = browser()
    first, second, third = _files(pdf_bytes, 3)
    for opened in (first, first, second, first):
        # One opened again, as a browser does once the server's hour is up, isn't counted again.
        assert_equal(upload(reader, opened).status_code, 201, "opened")
    refused = upload(reader, third)
    assert_problem(refused, "free_documents_used", 403)
    assert_in("opens 3 a month", refused.json()["detail"], "says what an account opens")
    assert_equal(upload(browser(), third).status_code, 201, "another browser has its own")


def test_a_file_that_doesnt_open_costs_none_of_the_allowance(
    metered_mail, browser: Callable[[], TestClient], pdf_bytes
):
    reader = browser()
    damaged = b"%PDF-1.7\nnot a PDF past its first line\n"
    assert_equal(upload(reader, damaged).status_code // 100, 4, "refused")
    for opened in _files(pdf_bytes, 2):
        assert_equal(upload(reader, opened).status_code, 201, "both free ones are left")


def test_signed_in_an_account_opens_its_months_documents_starting_fresh(
    metered_mail, browser: Callable[[], TestClient], pdf_bytes
):
    """Signing up is rewarded: what the browser opened without an account isn't carried over."""
    reader = browser()
    files = _files(pdf_bytes, 6)
    for opened in files[:2]:
        upload(reader, opened)
    _sign_in(reader, metered_mail)
    for opened in (*files[:2], files[2]):
        assert_equal(upload(reader, opened).status_code, 201, "this month's three")
    assert_equal(upload(reader, files[0]).status_code, 201, "one counted, opened again")
    assert_problem(upload(reader, files[3]), "monthly_documents_used", 403)
    # The same account in another browser has used the same month.
    elsewhere = browser()
    _sign_in(elsewhere, metered_mail)
    assert_problem(upload(elsewhere, files[4]), "monthly_documents_used", 403)


def test_an_unlimited_friend_opens_as_many_as_they_like(
    metered_mail, app: FastAPI, browser: Callable[[], TestClient], pdf_bytes
):
    app.state.accounts.users.invite(_EMAIL, now=0, is_unlimited=True)
    reader = browser()
    _sign_in(reader, metered_mail)
    for opened in _files(pdf_bytes, 5):
        assert_equal(upload(reader, opened).status_code, 201, "past the month's three")


def test_a_months_count_starts_afresh_in_the_next(tmp_path):
    """The API can't wait a month, so the table is asked directly."""
    users = open_users(tmp_path / "users.sqlite3")
    user = users.joined(_EMAIL, now=0)
    users.count(user.id, "a", month="2026-10")
    users.count(user.id, "b", month="2026-10")
    assert_equal(users.counted_in(user.id, month="2026-10"), {"a", "b"}, "October's")
    assert_equal(users.counted_in(user.id, month="2026-11"), set(), "November's, before any")
    users.count(user.id, "c", month="2026-11")
    assert_equal(users.counted_in(user.id, month="2026-11"), {"c"}, "November's, after one")


@pytest.mark.parametrize(
    ("named", "refused_for"),
    [
        (
            {"SQUIDPDF_FREE_DOCUMENTS": "3", "SQUIDPDF_DOCUMENTS_A_MONTH": "10"},
            "SQUIDPDF_DATABASE",
        ),
        ({"SQUIDPDF_DATABASE": "users.sqlite3", "SQUIDPDF_FREE_DOCUMENTS": "3"}, "neither"),
        (
            {
                "SQUIDPDF_DATABASE": "users.sqlite3",
                "SQUIDPDF_FREE_DOCUMENTS": "three",
                "SQUIDPDF_DOCUMENTS_A_MONTH": "10",
            },
            "not a number",
        ),
    ],
    ids=["with no accounts", "with one number of two", "with a word for a number"],
)
def test_an_allowance_set_half_or_without_accounts_stops_the_start(
    tmp_path, monkeypatch, named, refused_for
):
    """Metering asks for an account, so a server keeping none can't meter: said as it starts."""
    accounts_need = {
        "SQUIDPDF_SECRET": _SECRET.decode(),
        "SQUIDPDF_SITE": _SITE,
        "SQUIDPDF_SMTP_HOST": "mail.example.com",
        "SQUIDPDF_SMTP_PORT": "587",
        "SQUIDPDF_SMTP_SECURITY": "starttls",
        "SQUIDPDF_SMTP_USER": "squid",
        "SQUIDPDF_SMTP_PASSWORD": "secret",
        "SQUIDPDF_MAIL_FROM": "sign-in@example.com",
    }
    monkeypatch.delenv("SQUIDPDF_DATABASE", raising=False)
    for name, value in (accounts_need | named).items():
        monkeypatch.setenv(
            name, str(tmp_path / value) if name == "SQUIDPDF_DATABASE" else value
        )
    with pytest.raises(AccountsUnset, match=refused_for):
        create_app()


def test_invite_only_a_removed_friend_is_let_go_from_every_browser_at_once(
    invited_mail, app: FastAPI, browser: Callable[[], TestClient], pdf_bytes
):
    users = app.state.accounts.users
    users.invite(_EMAIL, now=0, is_unlimited=False)
    laptop, phone = browser(), browser()
    for signed_in in (laptop, phone):
        _sign_in(signed_in, invited_mail)
        assert_equal(upload(signed_in, pdf_bytes).status_code, 201, "in, before removal")
    assert_true(users.remove(_EMAIL), "the account was there to remove")
    for signed_in in (laptop, phone):
        assert_problem(upload(signed_in, pdf_bytes), "invited_only", 401)
        assert_equal(signed_in.get("/api/account").json()["signed_in"], None, "signed out")
    laptop.post("/api/account/link", json={"email": _EMAIL})
    assert_equal(len(invited_mail.letters), 2, "no link for an address no longer invited")


def test_a_browser_signed_in_to_a_removed_account_counts_as_one_without_any(
    metered_mail, app: FastAPI, browser: Callable[[], TestClient], pdf_bytes
):
    """Never a server error: the meter finds no account, so the browser's own count applies."""
    reader = browser()
    _sign_in(reader, metered_mail)
    app.state.accounts.users.remove(_EMAIL)
    first, second, third = _files(pdf_bytes, 3)
    for opened in (first, second):
        assert_equal(upload(reader, opened).status_code, 201, "the free documents")
    assert_problem(upload(reader, third), "free_documents_used", 403)
