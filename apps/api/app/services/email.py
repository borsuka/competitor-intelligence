"""Email delivery.

Two implementations behind one protocol:

* :class:`SMTPEmailSender` — used when ``SMTP_HOST`` is configured.
* :class:`ConsoleEmailSender` — the default. Writes the message to the log, including
  any link it contains, so development and test environments remain fully usable without
  a mail server. It is labelled, never silent: a caller can always tell whether a message
  was delivered or merely printed.

Nothing here is called from a request handler. Sending is queued through Celery, because
an SMTP round trip inside a signup request makes registration as slow and as fragile as
the mail server on its worst day.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MIMEEmailMessage
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import ExternalServiceError
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    # Optional: every message here is written so the plain-text part stands on its own.
    html: str | None = None


class EmailSender(Protocol):
    name: str
    delivers: bool

    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Logs the message instead of sending it.

    ``delivers`` is False, which is what lets callers and the API be honest about whether
    a link actually reached anyone.
    """

    name = "console"
    delivers = False

    def send(self, message: EmailMessage) -> None:
        log.info(
            "email.not_sent_no_transport",
            to=message.to,
            subject=message.subject,
            # The body carries the token, and in development that is the only way to get
            # at it. It is logged deliberately, and only when no transport is configured.
            body=message.text,
        )


class SMTPEmailSender:
    """Plain SMTP with STARTTLS, or implicit TLS on port 465."""

    name = "smtp"
    delivers = True

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.smtp_host:
            raise ExternalServiceError("SMTP_HOST is not configured.", code="smtp_not_configured")
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self._from = settings.smtp_from_email
        self._timeout = 20.0

    def send(self, message: EmailMessage) -> None:
        mime = MIMEEmailMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        if message.html:
            mime.add_alternative(message.html, subtype="html")

        context = ssl.create_default_context()
        try:
            if self._port == 465:
                with smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=context
                ) as server:
                    self._authenticate(server)
                    server.send_message(mime)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                    server.starttls(context=context)
                    self._authenticate(server)
                    server.send_message(mime)
        except (smtplib.SMTPException, OSError) as exc:
            # Raised so the Celery task can retry. A failed verification email is worth
            # retrying; swallowing it would leave a user unable to confirm their account
            # with nothing in the logs to explain why.
            raise ExternalServiceError(
                "The email could not be sent.", code="smtp_send_failed"
            ) from exc

        log.info("email.sent", to=message.to, subject=message.subject)

    def _authenticate(self, server: smtplib.SMTP) -> None:
        if self._username and self._password:
            server.login(self._username, self._password)


def build_email_sender() -> EmailSender:
    """Pick the sender the configuration implies."""
    settings = get_settings()
    if settings.smtp_host:
        return SMTPEmailSender()
    return ConsoleEmailSender()


def transport_is_configured() -> bool:
    """Whether mail actually leaves the building.

    Used by the API to decide whether returning a token in the response is a development
    affordance or a security hole.
    """
    return bool(get_settings().smtp_host)


# ------------------------------------------------------------------- templates


def _wrap(title: str, body: str, action_url: str | None, action_label: str | None) -> str:
    """Minimal HTML.

    Deliberately plain: mail clients render a subset of CSS from a decade ago, and a
    transactional message needs to be legible, not designed. Inline styles only, no
    external assets, and the link is a real anchor so it survives plain-text conversion.
    """
    button = (
        f'<p style="margin:24px 0"><a href="{action_url}" '
        'style="background:#2f5bd8;color:#fff;padding:10px 18px;border-radius:6px;'
        'text-decoration:none;display:inline-block">'
        f"{action_label}</a></p>"
        if action_url and action_label
        else ""
    )
    link = (
        f'<p style="color:#6b7280;font-size:13px">If the button does not work, paste this '
        f'into your browser:<br><span style="word-break:break-all">{action_url}</span></p>'
        if action_url
        else ""
    )
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'max-width:520px;margin:0 auto;padding:24px;color:#111827">'
        f'<h1 style="font-size:18px;margin:0 0 12px">{title}</h1>'
        f'<div style="font-size:14px;line-height:1.6;color:#374151">{body}</div>'
        f"{button}{link}"
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">'
        '<p style="color:#9ca3af;font-size:12px">Sentinel — competitor intelligence</p>'
        "</div>"
    )


def verification_email(*, to: str, name: str, token: str) -> EmailMessage:
    url = f"{get_settings().public_web_url}/verify-email?token={token}"
    text = (
        f"Hello {name},\n\n"
        "Confirm your email address to finish setting up your Sentinel account:\n\n"
        f"{url}\n\n"
        "The link is valid for 24 hours and can be used once.\n\n"
        "If you did not create an account, you can ignore this message.\n"
    )
    html = _wrap(
        "Confirm your email address",
        f"<p>Hello {name},</p><p>Confirm your address to finish setting up your account. "
        "The link is valid for 24 hours and can be used once.</p>",
        url,
        "Confirm email",
    )
    return EmailMessage(to=to, subject="Confirm your Sentinel account", text=text, html=html)


def password_reset_email(*, to: str, name: str, token: str) -> EmailMessage:
    url = f"{get_settings().public_web_url}/reset-password?token={token}"
    text = (
        f"Hello {name},\n\n"
        "Somebody asked to reset the password for your Sentinel account. "
        "If it was you, use this link:\n\n"
        f"{url}\n\n"
        "The link is valid for one hour and can be used once. Changing your password "
        "signs out every other device.\n\n"
        "If it was not you, no action is needed — your password has not changed.\n"
    )
    html = _wrap(
        "Reset your password",
        f"<p>Hello {name},</p><p>Somebody asked to reset the password for your account. "
        "The link is valid for one hour and can be used once, and changing your password "
        "signs out every other device.</p>"
        "<p>If it was not you, no action is needed — your password has not changed.</p>",
        url,
        "Choose a new password",
    )
    return EmailMessage(to=to, subject="Reset your Sentinel password", text=text, html=html)


def invitation_email(
    *, to: str, organization_name: str, inviter_name: str | None, token: str
) -> EmailMessage:
    url = f"{get_settings().public_web_url}/invite?token={token}"
    who = f"{inviter_name} has" if inviter_name else "You have been"
    text = (
        f"{who} invited you to join {organization_name} on Sentinel.\n\n"
        f"{url}\n\n"
        "Sign in with this email address, or create an account with it, and the "
        "invitation will be waiting. The link is valid for seven days.\n"
    )
    html = _wrap(
        f"Join {organization_name}",
        f"<p>{who} invited you to join <strong>{organization_name}</strong> on Sentinel.</p>"
        "<p>Sign in with this email address, or create an account with it, and the "
        "invitation will be waiting. The link is valid for seven days.</p>",
        url,
        "Accept invitation",
    )
    return EmailMessage(
        to=to, subject=f"You have been invited to {organization_name}", text=text, html=html
    )


def change_alert_email(*, to: str, title: str, body: str | None, competitor: str) -> EmailMessage:
    text = f"{title}\n\n{body or ''}\n\nCompetitor: {competitor}\n"
    html = _wrap(
        title,
        f"<p>{body or ''}</p><p style='color:#6b7280'>Competitor: {competitor}</p>",
        None,
        None,
    )
    return EmailMessage(to=to, subject=title[:180], text=text, html=html)


def queue(kind: str, **params: object) -> None:
    """Hand a message to the worker.

    Imported locally and failure-tolerant for the same reason job dispatch is: a broker
    that is briefly unreachable should not turn a successful registration into a 500. The
    account exists either way, and the user can request another link.
    """
    try:
        from app.workers.tasks import send_email_task

        send_email_task.delay(kind, params)
    except Exception as exc:  # never fail the request that triggered it
        log.warning("email.queue_failed", kind=kind, error=str(exc)[:200])


__all__ = [
    "ConsoleEmailSender",
    "EmailMessage",
    "EmailSender",
    "SMTPEmailSender",
    "build_email_sender",
    "change_alert_email",
    "invitation_email",
    "password_reset_email",
    "queue",
    "transport_is_configured",
    "verification_email",
]
