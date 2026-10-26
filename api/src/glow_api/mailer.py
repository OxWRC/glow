"""Plain-text outbound mail over SMTP, configured by GLOW_SMTP_* (the same
relay ODK Central uses). Stdlib only, so any standard relay works."""

import smtplib
import ssl
from email.message import EmailMessage

from glow_api.settings import settings


def configured() -> bool:
    return bool(settings.SMTP_HOST)


def send_mail(to: str, subject: str, body: str) -> None:
    """Send one message; raises (OSError/smtplib.SMTPException) on failure."""
    if not configured():
        raise RuntimeError("SMTP is not configured (GLOW_SMTP_HOST)")
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    if settings.SMTP_SECURE:
        conn = smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=10, context=context
        )
    else:
        conn = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
    with conn:
        if not settings.SMTP_SECURE:
            # Opportunistic, like ODK's nodemailer default: dev sinks (mailpit)
            # don't offer STARTTLS.
            conn.ehlo()
            if conn.has_extn("starttls"):
                conn.starttls(context=context)
        if settings.SMTP_USER:
            conn.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        conn.send_message(msg)
