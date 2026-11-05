"""How long a sign-in lasts, how long its link works, what a link may be sent to, and counts."""

from __future__ import annotations

# A link works this long: enough to switch to the mail app and back. Signed, never stored,
# it can't be taken back once sent, so its life is the only bound on a link left in an inbox.
LINK_VALID_S = 15 * 60
# A browser stays signed in this long without asking again: a month, so someone who fixes
# a document every few weeks finds themself still signed in.
SIGNED_IN_S = 30 * 24 * 60 * 60
# The files a browser opened without an account are kept counted this long: a year, past
# which someone back with no account is as good as new.
COUNTED_KEPT_S = 365 * 24 * 60 * 60
# How long the mailer has to answer, each step: a hung one holds a thread, not the app.
MAIL_TIMEOUT_S = 20
# Letters sent at once. A link waits its turn behind them, so a hung mail server holds
# these alone, never the threads the server's disk work shares: a few, as links are rare.
MAIL_THREADS = 2
# Longest address a mail server takes (RFC 5321's path, less its brackets).
MAX_EMAIL_CHARS = 254
