"""Turn database documents into the JSON the React app reads.

Dates are ISO strings (`2026-09-21`), timestamps are ISO with a UTC offset.
Only what a screen needs is sent; a manager never receives other teams' data.
"""

from datetime import date, datetime

from app.config import Settings
from app.core.editing import read_only_reason
from app.core.timeutil import as_utc, to_local


def iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    return value.isoformat()


def cycle_json(cycle: dict, settings: Settings, role: str | None = None) -> dict:
    deadline = cycle.get("correction_deadline")
    data = {
        "id": cycle["id"],
        "name": cycle["name"],
        "start_date": iso(cycle["start_date"].date()) if cycle.get("start_date") else None,
        "end_date": iso(cycle["end_date"].date()) if cycle.get("end_date") else None,
        "status": cycle.get("status", "open"),
        "active": cycle.get("active", False),
        "correction_deadline": iso(deadline),
        # The day HR picked, in local time (the deadline is 23:59:59 on that day).
        "deadline_date": to_local(deadline, settings.timezone).date().isoformat() if deadline else None,
        "uploaded_at": iso(cycle.get("uploaded_at")),
        "uploaded_by": cycle.get("uploaded_by"),
        "original_filename": cycle.get("original_filename"),
        "stats": cycle.get("stats", {}),
    }
    if role is not None:
        reason = read_only_reason(cycle, role, settings)
        data.update(role=role, read_only_reason=reason, can_edit=reason is None, dates=cycle["dates"])
    return data


def employee_json(emp: dict, include_manager: bool) -> dict:
    data = {
        "employee_id": emp["employee_id"],
        "employee_name": emp.get("employee_name") or "",
        "department": emp.get("department"),
        "emp_status": emp.get("emp_status"),
        "lwd": iso(emp["lwd"].date()) if emp.get("lwd") else None,
        "days": emp.get("days", {}),
        "original_days": emp.get("original_days", {}),
        "calculated_days": emp.get("calculated_days", {}),
        "schedule": emp.get("schedule", {}),
        "worked_seconds": emp.get("worked_seconds", {}),
    }
    if include_manager:
        data.update(manager_name=emp.get("manager_name"), manager_email=emp.get("manager_email"))
    return data


def change_json(change: dict, names: dict[str, str]) -> dict:
    return {
        "changed_at": iso(change["changed_at"]),
        "employee_id": change["employee_id"],
        "employee_name": names.get(change["employee_id"], ""),
        "manager_email": change.get("manager_email"),
        "date": change["date"],
        "old_value": change.get("old_value"),
        "new_value": change["new_value"],
        "changed_by_email": change["changed_by_email"],
        "changed_by_role": change["changed_by_role"],
        "comment": change.get("comment"),
    }


def exception_json(issue: dict) -> dict:
    return {key: issue.get(key) for key in ("type", "severity", "message", "employee_id", "employee_name", "date")}
