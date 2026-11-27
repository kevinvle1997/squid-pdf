from __future__ import annotations

# Enough to switch to the mail app and back; never stored, so its life is a link's only bound.
LINK_VALID_S = 15 * 60
# A month, so someone who fixes a document every few weeks is still signed in.
SIGNED_IN_S = 30 * 24 * 60 * 60
# A year, past which someone back with no account is as good as new.
COUNTED_KEPT_S = 365 * 24 * 60 * 60
# Per step: a hung mailer holds a thread, not the app.
MAIL_TIMEOUT_S = 20
# A few, as links are rare: a hung mail server holds these alone, never the disk threads.
MAIL_THREADS = 2
# Stripe charges a renewal up to an hour after the month ends: twice that lets the webhook land.
RENEWAL_LEEWAY_S = 2 * 60 * 60
# Pinned, so Stripe's dashboard changes nothing here; in it, a paid period is its items'.
STRIPE_API_VERSION = "2025-03-31.basil"
# Far past a healthy answer, short enough that every try fits in under a minute.
STRIPE_TIMEOUT_S = 10
# A few, as payments are rare: a hung Stripe holds these alone, never the upload threads.
BILLING_THREADS = 4
# Three, as Stripe's own libraries try; each try sends the same idempotency key.
STRIPE_ATTEMPTS = 3
# As Stripe's own libraries wait, doubled each try and cut by up to half at random.
STRIPE_FIRST_WAIT_S = 0.5
# Stripe's least: an old tab, finished later, would start a second subscription.
CHECKOUT_VALID_S = 30 * 60
# As Stripe's own libraries allow, so an old one sent again is refused.
STRIPE_SIGNATURE_AGE_S = 5 * 60
# Longest address a mail server takes (RFC 5321's path, less its brackets).
MAX_EMAIL_CHARS = 254
