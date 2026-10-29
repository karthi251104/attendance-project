from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class EmailMessage:
    to: str
    subject: str
    html_body: str
    cc: list[str] = field(default_factory=list)


class SendError(Exception):
    pass


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None:
        """Send one email or raise SendError."""
        ...
