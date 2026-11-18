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
# Stripe charges a renewal up to an hour after the paid month ends, so paid time runs this
# much past it: the charge lands before the limit comes back. A failed charge gets no more.
RENEWAL_LEEWAY_S = 2 * 60 * 60
# Stripe answers in the shape of this version, pinned so a change in Stripe's dashboard changes
# nothing here. In it, a subscription's paid period is its items'.
STRIPE_API_VERSION = "2025-03-31.basil"
# How long Stripe has to answer, each step: a hung one holds a billing thread, not the app.
STRIPE_TIMEOUT_S = 10
# Calls to Stripe at once. A webhook waits its turn behind them, so a hung Stripe holds these
# alone, never the threads every upload and read share: a few, as payments are rare.
BILLING_THREADS = 4
# Tries at one call before saying payments can't be reached. Each try sends the same request,
# its idempotency key too, so a retry never does twice what an earlier try did.
STRIPE_ATTEMPTS = 3
# The wait before the second try, doubled before each after it and cut by up to half at
# random, so retries from many requests don't land on Stripe together.
STRIPE_FIRST_WAIT_S = 0.5
# How old a webhook's signature may be: a copy of an old one, sent again by anyone, is refused.
STRIPE_SIGNATURE_AGE_S = 5 * 60
# Longest address a mail server takes (RFC 5321's path, less its brackets).
MAX_EMAIL_CHARS = 254
