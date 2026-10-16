"""
PulseO: email delivery
======================
One email per deck, with the .pptx attached, sent over SMTP with STARTTLS (Gmail:
smtp.gmail.com:587 with an app password). Nothing is stored anywhere else: the deck
exists in memory during the run and in the inbox.
"""

import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage

PPTX_TYPE = ("application", "vnd.openxmlformats-officedocument.presentationml.presentation")


class MailError(Exception):
    """Sending failed. The message is scrubbed by the caller before it is logged."""


@dataclass
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str

    def sensitive_values(self) -> list:
        return [v for v in (self.user, self.password) if v]


def settings_from_env(env) -> "SmtpSettings | None":
    """SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD; None when incomplete."""
    host = (env.get("SMTP_HOST") or "").strip()
    user = (env.get("SMTP_USER") or "").strip()
    password = env.get("SMTP_PASSWORD") or ""
    if not (host and user and password):
        return None
    try:
        port = int((env.get("SMTP_PORT") or "587").strip())
    except ValueError:
        return None
    return SmtpSettings(host, port, user, password)


def resolve_recipients(recipients: list, env) -> list:
    """Addresses as written, or `env:NAME` replaced by the value of that environment variable,
    so an address can live in a secret instead of the configuration."""
    out = []
    for r in recipients:
        m = re.fullmatch(r"env:([A-Z][A-Z0-9_]*)", r)
        value = (env.get(m.group(1)) or "").strip() if m else r
        if value:
            out.append(value)
    return out


def safe_filename(label: str, period: str) -> str:
    """'<label>-SEO-Report-<period>.pptx' with safe characters only."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._") or "Site"
    return f"{base}-SEO-Report-{period}.pptx"


def build_message(sender: str, recipients: list, subject: str, body: str, filename: str,
                  payload: bytes) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(payload, maintype=PPTX_TYPE[0], subtype=PPTX_TYPE[1], filename=filename)
    return msg


def send(settings: SmtpSettings, message: EmailMessage, attempts: int = 2, wait: float = 5.0,
         sleep=None, smtp_class=None):
    """Sends the message; after a failure it waits and tries once more, then raises MailError."""
    last = None
    for attempt in range(attempts):
        try:
            with (smtp_class or smtplib.SMTP)(settings.host, settings.port, timeout=60) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(settings.user, settings.password)
                server.send_message(message)
            return
        except (smtplib.SMTPException, OSError) as e:
            last = e
            if attempt + 1 < attempts:
                (sleep or time.sleep)(wait)
    raise MailError(f"{type(last).__name__}: {last}")
