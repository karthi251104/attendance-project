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


# ---------- SMTP sender (Gmail) ----------

class FakeSMTP:
    """Stands in for smtplib.SMTP; records what the sender does."""

    instances: list["FakeSMTP"] = []
    fail_with: list[Exception] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))
        assert password == "abcdefghijklmnop"  # the spaces Gmail shows are dropped
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with.pop(0)

    def send_message(self, mime):
        self.sent.append(mime)


def smtp_sender(sleeps: list[float]):
    import smtplib  # noqa: F401 - the exceptions below come from here

    from app.email.smtp_sender import SmtpSender

    FakeSMTP.instances, FakeSMTP.fail_with = [], []
    settings = Settings(_env_file=None, email_mode="smtp", sender_mailbox="hr.box@gmail.com",
                        smtp_username="hr.box@gmail.com", smtp_password="abcd efgh ijkl mnop", email_per_second=2)
    return SmtpSender(settings, smtp_factory=FakeSMTP, sleep=sleeps.append)


def test_smtp_sends_html_mail_over_tls() -> None:
    smtp_sender([]).send(MESSAGE)
    [server] = FakeSMTP.instances
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["starttls", ("login", "hr.box@gmail.com")]
    [mime] = server.sent
    assert (mime["From"], mime["To"], mime["Cc"], mime["Subject"]) == ("hr.box@gmail.com", "m@example.com", "c@example.com", "S")
    assert mime.get_body(("html",)).get_content().strip() == "<p>x</p>"


def test_smtp_wrong_password_is_a_clear_error() -> None:
    import smtplib

    sender = smtp_sender([])
    FakeSMTP.fail_with = [smtplib.SMTPAuthenticationError(535, b"bad")]
    with pytest.raises(SendError, match="App Password"):
        sender.send(MESSAGE)


def test_smtp_retries_temporary_failures() -> None:
    import smtplib

    sleeps: list[float] = []
    sender = smtp_sender(sleeps)
    FakeSMTP.fail_with = [smtplib.SMTPServerDisconnected("gone")]
    sender.send(MESSAGE)
    assert len(FakeSMTP.instances) == 2 and FakeSMTP.instances[1].sent
    assert 2 in sleeps


def test_smtp_settings_are_checked() -> None:
    problems = Settings(_env_file=None, email_mode="smtp").startup_problems()
    assert any("SENDER_MAILBOX" in p for p in problems) and any("SMTP_USERNAME" in p for p in problems)
    ok = Settings(_env_file=None, email_mode="smtp", sender_mailbox="a@gmail.com", smtp_username="a@gmail.com", smtp_password="x")
    assert ok.startup_problems() == []


def test_email_explains_how_to_sign_in() -> None:
    cycle = {"id": "abc", "name": "Cycle", "correction_deadline": None}
    password_mail = build_message(cycle, "M", "m@example.com", 1, Settings(_env_file=None)).html_body
    assert "m@example.com" in password_mail and "password HR gave you" in password_mail
    entra_mail = build_message(cycle, "M", "m@example.com", 1, Settings(_env_file=None, auth_mode="entra")).html_body
    assert "Microsoft account" in entra_mail and "password" not in entra_mail


# ---------- Brevo sender (HTTPS API) ----------

def brevo_sender(handler, sleeps: list[float]):
    from app.email.brevo_sender import BrevoSender

    settings = Settings(_env_file=None, email_mode="brevo", sender_mailbox="hr.box@gmail.com",
                        brevo_api_key="xkeysib-test", email_per_second=2)
    return BrevoSender(settings, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)


def test_brevo_sends_mail() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"messageId": "<1@brevo>"})

    brevo_sender(handler, []).send(MESSAGE)
    [request] = requests
    assert str(request.url) == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == "xkeysib-test"
    body = json.loads(request.content)
    assert body["sender"] == {"email": "hr.box@gmail.com", "name": "HR Attendance"}
    assert body["to"] == [{"email": "m@example.com"}] and body["cc"] == [{"email": "c@example.com"}]
    assert (body["subject"], body["htmlContent"]) == ("S", "<p>x</p>")


def test_brevo_wrong_key_is_a_clear_error() -> None:
    with pytest.raises(SendError, match="API key"):
        brevo_sender(lambda request: httpx.Response(401, json={"message": "Key not found"}), []).send(MESSAGE)


def test_brevo_unverified_sender_shows_brevos_message() -> None:
    error = httpx.Response(400, json={"code": "invalid_parameter", "message": "Sender is not valid"})
    with pytest.raises(SendError, match="Sender is not valid"):
        brevo_sender(lambda request: error, []).send(MESSAGE)


def test_brevo_retries_429_and_5xx() -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(502), httpx.Response(201)])
    sleeps: list[float] = []
    brevo_sender(lambda request: next(responses), sleeps).send(MESSAGE)
    assert 3.0 in sleeps


def test_brevo_settings_are_checked() -> None:
    problems = Settings(_env_file=None, email_mode="brevo").startup_problems()
    assert any("BREVO_API_KEY" in p for p in problems) and any("SENDER_MAILBOX" in p for p in problems)
    ok = Settings(_env_file=None, email_mode="brevo", sender_mailbox="a@gmail.com", brevo_api_key="k")
    assert ok.startup_problems() == []
