"""Emails to managers (console and Microsoft Graph; Graph is mocked, no network)."""

import json

import httpx
import pytest

from app.config import Settings
from app.email.base import EmailMessage, SendError
from app.email.graph_sender import GraphSender
from app.email.service import build_message, send_manager_emails
from tests.conftest import MANAGER_ONE, MANAGER_TWO


def test_send_to_all_managers(uploaded) -> None:
    portal, cycle_id = uploaded
    assert portal.post(f"/api/cycles/{cycle_id}/emails").json() == {"queued": 2}
    assert sorted(m.to for m in portal.sender.sent) == [MANAGER_ONE, MANAGER_TWO]
    log = portal.repo.latest_email_by_manager(cycle_id)
    assert {email: entry["status"] for email, entry in log.items()} == {MANAGER_ONE: "sent", MANAGER_TWO: "sent"}
    managers = portal.get(f"/api/cycles/{cycle_id}/managers").json()["managers"]
    assert {m["email_status"] for m in managers} == {"sent"} and all(m["email_sent_at"] for m in managers)


def test_resend_to_one_manager(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.post(f"/api/cycles/{cycle_id}/emails", {"manager_email": MANAGER_TWO})
    assert [m.to for m in portal.sender.sent] == [MANAGER_TWO]


def test_email_content(uploaded) -> None:
    portal, cycle_id = uploaded
    portal.put(f"/api/cycles/{cycle_id}/deadline", {"deadline": "2026-11-05"})
    portal.post(f"/api/cycles/{cycle_id}/emails", {"manager_email": MANAGER_ONE})
    [message] = portal.sender.sent
    assert message.subject == "Please review your team's attendance: 20 Sep 2026 - 19 Oct 2026"
    body = message.html_body
    assert "Dear Fake Manager One," in body
    assert "your 3 team members" in body
    assert f"http://testserver/team/{cycle_id}" in body
    assert "Corrections are open until 05/11/2026 23:59." in body
    assert "This is an automated email from HR." in body
    assert "Fake Employee" not in body and "10000001" not in body  # no employee data in the email


def test_invalid_manager_email_is_logged_as_failed(portal, make_workbook) -> None:
    from tests.conftest import HR
    portal.login(HR)
    cycle_id = portal.upload(make_workbook(hc_overrides={20000001: {"Email ID": "not-an-email"}}))
    portal.post(f"/api/cycles/{cycle_id}/emails")
    log = portal.repo.latest_email_by_manager(cycle_id)
    assert log["not-an-email"]["status"] == "failed"
    assert "not-an-email" not in [m.to for m in portal.sender.sent]


def test_one_failure_does_not_stop_the_batch(uploaded) -> None:
    portal, cycle_id = uploaded

    class FlakySender:
        def __init__(self):
            self.sent = []

        def send(self, message):
            if message.to == MANAGER_ONE:
                raise SendError("mailbox full")
            self.sent.append(message)

    sender = FlakySender()
    counts = send_manager_emails(portal.repo, sender, portal.repo.get_cycle(cycle_id), Settings(_env_file=None))
    assert counts == {"sent": 1, "failed": 1}
    assert portal.repo.latest_email_by_manager(cycle_id)[MANAGER_ONE]["error"] == "mailbox full"


def test_cc_from_config() -> None:
    settings = Settings(_env_file=None, email_cc="hr.box@example.com, other@example.com")
    cycle = {"id": "abc", "name": "Cycle", "correction_deadline": None}
    message = build_message(cycle, "M", "m@example.com", 1, settings)
    assert message.cc == ["hr.box@example.com", "other@example.com"]
    assert "until" not in message.html_body  # no deadline line when none is set
    assert "your 1 team member " in message.html_body


# ---------- Graph sender ----------

def graph_sender(handler, sleeps: list[float]):
    settings = Settings(_env_file=None, email_mode="graph", sender_mailbox="hr.attendance@example.com", email_per_second=2)
    return GraphSender(settings, http=httpx.Client(transport=httpx.MockTransport(handler)),
                       token_provider=lambda: "token-123", sleep=sleeps.append)


MESSAGE = EmailMessage(to="m@example.com", subject="S", html_body="<p>x</p>", cc=["c@example.com"])


def test_graph_sends_mail() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202)

    graph_sender(handler, []).send(MESSAGE)
    [request] = requests
    assert str(request.url) == "https://graph.microsoft.com/v1.0/users/hr.attendance@example.com/sendMail"
    assert request.headers["authorization"] == "Bearer token-123"
    body = json.loads(request.content)
    assert body["message"]["toRecipients"] == [{"emailAddress": {"address": "m@example.com"}}]
    assert body["message"]["ccRecipients"] == [{"emailAddress": {"address": "c@example.com"}}]
    assert body["message"]["body"]["contentType"] == "HTML"


def test_graph_retries_429_with_retry_after() -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(503), httpx.Response(202)])
    sleeps: list[float] = []
    graph_sender(lambda request: next(responses), sleeps).send(MESSAGE)
    assert 7.0 in sleeps  # honoured Retry-After


def test_graph_gives_up_on_client_error() -> None:
    error = httpx.Response(400, json={"error": {"message": "Invalid recipient"}})
    with pytest.raises(SendError, match="Invalid recipient"):
        graph_sender(lambda request: error, []).send(MESSAGE)


def test_graph_gives_up_after_retries() -> None:
    with pytest.raises(SendError):
        graph_sender(lambda request: httpx.Response(429, headers={"Retry-After": "1"}), []).send(MESSAGE)


def test_graph_throttles(monkeypatch) -> None:
    sleeps: list[float] = []
    sender = graph_sender(lambda request: httpx.Response(202), sleeps)
    sender.send(MESSAGE)
    sender.send(MESSAGE)
    assert sleeps and 0 < sleeps[0] <= 0.5  # second email waits for the 2-per-second limit
