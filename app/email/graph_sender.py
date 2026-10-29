"""EMAIL_MODE=graph: send through Microsoft Graph `POST /users/{SENDER_MAILBOX}/sendMail`.

Uses the app registration's application permission Mail.Send (client credentials).
The tenant admin should restrict the app to the sender mailbox only (see README).

Sending is throttled (EMAIL_PER_SECOND, default 2). HTTP 429 and 5xx responses are
retried, honouring the Retry-After header.
"""

import logging
import threading
import time

import httpx
import msal

from app.config import Settings
from app.email.base import EmailMessage, SendError

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
MAX_ATTEMPTS = 5


class GraphSender:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, token_provider=None,
                 sleep=time.sleep) -> None:
        self._mailbox = settings.sender_mailbox
        self._interval = 1.0 / settings.email_per_second if settings.email_per_second > 0 else 0
        self._http = http or httpx.Client(timeout=30)
        self._sleep = sleep
        self._last_sent = 0.0
        self._lock = threading.Lock()
        self._token_provider = token_provider or self._msal_token_provider(settings)

    @staticmethod
    def _msal_token_provider(settings: Settings):
        app = msal.ConfidentialClientApplication(
            client_id=settings.entra_client_id,
            client_credential=settings.entra_client_secret,
            authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
        )

        def token() -> str:
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])  # cached by MSAL
            if "access_token" not in result:
                raise SendError(f"Could not get a Graph token: {result.get('error_description') or result.get('error')}")
            return result["access_token"]

        return token

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_sent + self._interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_sent = time.monotonic()

    def send(self, message: EmailMessage) -> None:
        payload = {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "HTML", "content": message.html_body},
                "toRecipients": [{"emailAddress": {"address": message.to}}],
                "ccRecipients": [{"emailAddress": {"address": cc}} for cc in message.cc],
            },
            "saveToSentItems": True,
        }
        url = f"{GRAPH_URL}/users/{self._mailbox}/sendMail"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._http.post(url, json=payload, headers={"Authorization": f"Bearer {self._token_provider()}"})
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise SendError(f"Network error: {exc}") from exc
                self._sleep(2 ** attempt)
                continue
            if response.status_code == 202:
                return
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = _retry_after(response, default=2 ** attempt)
                log.warning("Graph sendMail returned %s; retry %d in %ss", response.status_code, attempt, retry_after)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(retry_after)
                    continue
            raise SendError(f"Graph returned HTTP {response.status_code}: {_graph_error(response)}")
        raise SendError("Gave up after several retries.")


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(float(response.headers.get("Retry-After", default)), 120)
    except ValueError:
        return default


def _graph_error(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except Exception:  # not JSON or unexpected shape
        return response.text[:200]
