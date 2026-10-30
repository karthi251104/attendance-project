"""EMAIL_MODE=smtp: send through any SMTP server, for example Gmail or Outlook.com.

Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USERNAME=<the Gmail address>, and SMTP_PASSWORD=<an App
Password> (Google Account > Security > 2-Step Verification > App passwords; the normal Gmail password does not
work). SENDER_MAILBOX is the From address and is usually the same as SMTP_USERNAME.

Sending is throttled (EMAIL_PER_SECOND) like the Graph sender. Temporary server errors are retried a few times.
"""

import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid

from app.config import Settings
from app.email.base import EmailMessage, SendError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class SmtpSender:
    def __init__(self, settings: Settings, smtp_factory=smtplib.SMTP, sleep=time.sleep) -> None:
        self._settings = settings
        self._factory = smtp_factory
        self._sleep = sleep
        self._interval = 1.0 / settings.email_per_second if settings.email_per_second > 0 else 0
        self._last_sent = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_sent + self._interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_sent = time.monotonic()

    def _mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self._settings.sender_mailbox
        mime["To"] = message.to
        if message.cc:
            mime["Cc"] = ", ".join(message.cc)
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=True)
        mime["Message-ID"] = make_msgid(domain=self._settings.sender_mailbox.split("@")[-1])
        mime.set_content("This email needs an email program that shows HTML. Please open it in Gmail or Outlook.")
        mime.add_alternative(message.html_body, subtype="html")
        return mime

    def _password(self) -> str:
        """Gmail shows App Passwords as 'abcd efgh ijkl mnop'; the spaces are not part of it."""
        password = self._settings.smtp_password
        return password.replace(" ", "") if self._settings.smtp_host.endswith("gmail.com") else password

    def send(self, message: EmailMessage) -> None:
        settings = self._settings
        mime = self._mime(message)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                with self._factory(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.login(settings.smtp_username, self._password())
                    smtp.send_message(mime)
                return
            except smtplib.SMTPAuthenticationError as exc:
                raise SendError("The mail server refused the username or password (for Gmail, use an App Password).") from exc
            except smtplib.SMTPRecipientsRefused as exc:
                raise SendError("The mail server refused this recipient address.") from exc
            except (smtplib.SMTPException, OSError) as exc:
                log.warning("SMTP send failed (attempt %d): %s", attempt, exc.__class__.__name__)
                if attempt == MAX_ATTEMPTS:
                    raise SendError(f"Could not send through {settings.smtp_host}: {exc.__class__.__name__}") from exc
                self._sleep(2 ** attempt)
