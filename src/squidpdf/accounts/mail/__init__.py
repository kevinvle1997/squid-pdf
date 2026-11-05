"""Account mail: each email in its reader's words, sent through the deployment's mailer."""

from __future__ import annotations

from squidpdf.accounts.mail.brevo import (
    BrevoMailer as BrevoMailer,
    brevo_mailer as brevo_mailer,
)
from squidpdf.accounts.mail.sending import (
    Letter as Letter,
    MailController as MailController,
    Mailer as Mailer,
)
from squidpdf.accounts.mail.smtp import Security as Security, SmtpMailer as SmtpMailer
