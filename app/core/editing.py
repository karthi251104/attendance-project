"""Who may edit what, and saving edits with an audit trail.

Rules:
  * A manager may see and edit only employees whose Manager Email equals their own email.
  * HR may see and edit every employee.
  * Nobody can edit a locked cycle or an upload that has been replaced by a newer one.
  * Managers cannot edit after the correction deadline; HR still can.
These checks run on the server for every request; the browser only mirrors them.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings
from app.core.timeutil import as_utc, fmt_datetime, now_utc
from app.db.repositories import Repository

log = logging.getLogger(__name__)

MAX_CHANGES_PER_SAVE = 10_000
MAX_COMMENT_LENGTH = 500


class EditError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class Change:
    employee_id: str
    date: str
    value: str
    old_value: str | None = None  # the value the user saw; used to detect a concurrent edit
    comment: str | None = None


@dataclass
class SaveResult:
    saved: int = 0
    unchanged: int = 0
    conflicts: list[dict] = field(default_factory=list)


def role_for(repo: Repository, cycle_id: str, email: str, is_hr: bool) -> str | None:
    """Return "hr", "manager" or None (no access) for this user in this cycle."""
    if is_hr:
        return "hr"
    if repo.is_manager(cycle_id, email):
        return "manager"
    return None


def read_only_reason(cycle: dict, role: str, settings: Settings, now: datetime | None = None) -> str | None:
    """Why the grid is read-only for this user, or None if they can edit."""
    now = now or now_utc()
    if not cycle.get("active", False):
        return "This upload has been replaced by a newer version and can no longer be edited."
    if cycle.get("status") == "locked":
        return "HR has locked this cycle. Corrections are closed."
    deadline = cycle.get("correction_deadline")
    if role == "manager" and deadline and now > as_utc(deadline):
        return f"The correction deadline ({fmt_datetime(deadline, settings.timezone)}) has passed."
    return None


def save_changes(
    repo: Repository, cycle: dict, user_email: str, role: str, changes: list[Change], settings: Settings,
) -> SaveResult:
    """Validate every change first (all or nothing), then apply them one by one with an audit entry."""
    reason = read_only_reason(cycle, role, settings)
    if reason:
        raise EditError(reason, status_code=403)
    if len(changes) > MAX_CHANGES_PER_SAVE:
        raise EditError(f"Too many changes in one save (maximum {MAX_CHANGES_PER_SAVE}).")

    cycle_id = cycle["id"]
    allowed = set(settings.allowed_status_list)
    dates = set(cycle["dates"])
    employees: dict[str, dict] = {}

    # 1. Validate everything before writing anything.
    for change in changes:
        change.value = (change.value or "").strip().upper()
        if change.value not in allowed:
            raise EditError(f"'{change.value}' is not an allowed status. Allowed: {', '.join(settings.allowed_status_list)}.")
        if change.date not in dates:
            raise EditError(f"{change.date} is not a date in this cycle.")
        if change.comment and len(change.comment) > MAX_COMMENT_LENGTH:
            raise EditError(f"Comments can be at most {MAX_COMMENT_LENGTH} characters.")
        if change.employee_id not in employees:
            emp = repo.get_employee(cycle_id, change.employee_id)
            # Same message for "not found" and "not your team", so IDs of other teams cannot be probed.
            if emp is None or (role == "manager" and emp.get("manager_email") != user_email):
                raise EditError("You do not have access to one or more of these employees.", status_code=403)
            employees[change.employee_id] = emp

    # 2. Apply.
    result = SaveResult()
    for change in changes:
        emp = employees[change.employee_id]
        current = emp["days"].get(change.date)
        expected = change.old_value if change.old_value is not None else current
        if change.value == current:
            result.unchanged += 1
            continue
        if expected != current or not repo.update_day(cycle_id, change.employee_id, change.date, current, change.value):
            fresh = repo.get_employee(cycle_id, change.employee_id) or emp
            result.conflicts.append({"employee_id": change.employee_id, "date": change.date,
                                     "current_value": fresh["days"].get(change.date)})
            continue
        emp["days"][change.date] = change.value
        repo.insert_change({
            "cycle_id": cycle_id,
            "employee_id": change.employee_id,
            "manager_email": emp.get("manager_email"),
            "date": change.date,
            "old_value": current,
            "new_value": change.value,
            "changed_by_email": user_email,
            "changed_by_role": role,
            "changed_at": now_utc(),
            "comment": (change.comment or "").strip() or None,
        })
        result.saved += 1

    log.info("Save: cycle=%s role=%s saved=%d unchanged=%d conflicts=%d",
             cycle_id, role, result.saved, result.unchanged, len(result.conflicts))
    return result
