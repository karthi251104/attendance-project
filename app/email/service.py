"""Emails to managers: one per manager email, containing only the team size and a link.

Employee data never goes into the email; it stays in the portal.
"""

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.core.grid import is_valid_email
from app.core.timeutil import fmt_datetime, now_utc
from app.db.repositories import Repository
from app.email.base import EmailMessage, EmailSender, SendError

log = logging.getLogger(__name__)

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))


def build_message(cycle: dict, manager_name: str, manager_email: str, team_size: int, settings: Settings) -> EmailMessage:
    deadline = cycle.get("correction_deadline")
    html = _env.get_template("manager_review.html").render(
        manager_name=manager_name or "Manager",
        team_size=team_size,
        cycle_name=cycle["name"],
        link=f"{settings.app_base_url.rstrip('/')}/team/{cycle['id']}",
        deadline=fmt_datetime(deadline, settings.timezone) if deadline else None,
    )
    return EmailMessage(
        to=manager_email,
        subject=f"Please review your team's attendance: {cycle['name']}",
        html_body=html,
        cc=settings.email_cc_list,
    )


def send_manager_emails(repo: Repository, sender: EmailSender, cycle: dict, settings: Settings,
                        only_email: str | None = None) -> dict[str, int]:
    """Send (or resend, with only_email) review emails and record each attempt in email_log."""
    counts = {"sent": 0, "failed": 0}
    for team in repo.managers(cycle["id"]):
        email = team["manager_email"]
        if only_email is not None and email != only_email:
            continue
        status, error = "sent", None
        if not is_valid_email(email):
            status, error = "failed", "Missing or invalid manager email"
        else:
            try:
                sender.send(build_message(cycle, team["manager_name"], email, team["team_size"], settings))
            except SendError as exc:
                status, error = "failed", str(exc)
            except Exception as exc:  # never let one manager stop the whole batch
                log.exception("Unexpected error sending email")
                status, error = "failed", f"Unexpected error: {exc.__class__.__name__}"
        repo.insert_email_log({
            "cycle_id": cycle["id"], "manager_email": email, "manager_name": team["manager_name"],
            "team_size": team["team_size"], "status": status, "error": error, "sent_at": now_utc(),
        })
        counts[status] += 1
    log.info("Emails for cycle=%s: sent=%d failed=%d", cycle["id"], counts["sent"], counts["failed"])
    return counts
