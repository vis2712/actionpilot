"""Sending email. "console" (default) only logs it, so the demo never emails real people by accident."""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("app.email")


def _send_smtp(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: list[str], subject: str, body: str, reply_to: str | None = None) -> str:
    """Returns how it was delivered ("console" or "smtp"). Raises on SMTP errors."""
    if settings.email_backend == "console":
        log.info("EMAIL (console backend, not sent)\nTo: %s\nSubject: %s\n\n%s", ", ".join(to), subject, body)
        return "console"
    msg = EmailMessage()
    msg["From"] = settings.email_from or settings.smtp_username
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    await asyncio.to_thread(_send_smtp, msg)  # smtplib is blocking
    return "smtp"
