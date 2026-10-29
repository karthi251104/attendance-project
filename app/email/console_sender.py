"""EMAIL_MODE=console: print emails to the log instead of sending them (development)."""

import logging

from app.email.base import EmailMessage

log = logging.getLogger(__name__)


class ConsoleSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []  # kept so tests and developers can inspect them

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)
        # Development only: this prints the recipient, which is never done in graph mode.
        log.info("[console email] to=%s cc=%s subject=%s\n%s", message.to, message.cc, message.subject, message.html_body)
