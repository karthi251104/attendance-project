"""EMAIL_MODE=brevo: send through Brevo's HTTPS API (https://www.brevo.com, free for 300 emails a day).

For hosts that block outgoing SMTP, such as Render's free plan: the API is plain HTTPS.
Needs BREVO_API_KEY (Brevo > SMTP & API > API keys) and SENDER_MAILBOX, which must be a sender you verified in
Brevo (Senders, domains & dedicated IPs > Senders). SENDER_NAME is the name recipients see.

Sending is throttled (EMAIL_PER_SECOND). HTTP 429 and 5xx responses are retried, honouring Retry-After.
"""

import logging
import threading
import time

import httpx

from app.config import Settings
from app.email.base import EmailMessage, SendError

log = logging.getLogger(__name__)

API_URL = "https://api.brevo.com/v3/smtp/email"
MAX_ATTEMPTS = 4


class BrevoSender:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, sleep=time.sleep) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=30)
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

    def send(self, message: EmailMessage) -> None:
        payload = {
            "sender": {"email": self._settings.sender_mailbox, "name": self._settings.sender_name},
            "to": [{"email": message.to}],
            "subject": message.subject,
            "htmlContent": message.html_body,
        }
        if message.cc:
            payload["cc"] = [{"email": cc} for cc in message.cc]
        headers = {"api-key": self._settings.brevo_api_key, "accept": "application/json"}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._http.post(API_URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise SendError(f"Could not reach Brevo: {exc.__class__.__name__}") from exc
                self._sleep(2 ** attempt)
                continue
            if response.status_code in (200, 201, 202):
                return
            if response.status_code == 401:
                raise SendError("Brevo refused the API key (check BREVO_API_KEY).")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    self._sleep(_retry_after(response, default=2 ** attempt))
                    continue
            raise SendError(f"Brevo returned HTTP {response.status_code}: {_brevo_error(response)}")
        raise SendError("Gave up after several retries.")


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(float(response.headers.get("Retry-After", default)), 120)
    except ValueError:
        return default


def _brevo_error(response: httpx.Response) -> str:
    try:
        return response.json().get("message") or response.text[:200]
    except Exception:  # not JSON
        return response.text[:200]
