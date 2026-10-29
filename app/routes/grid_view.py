"""Prepare employee documents for the grid template (manager view and HR "All employees")."""

from datetime import date

from app.core.rules import format_seconds
from app.core.timeutil import fmt_date
from app.core.totals import TOTAL_CODES, compute_totals


def date_headers(dates: list[str]) -> list[dict]:
    return [{"iso": iso, "label": date.fromisoformat(iso).strftime("%d/%m"),
             "weekday": date.fromisoformat(iso).strftime("%a")} for iso in dates]


def grid_rows(employees: list[dict], dates: list[str]) -> list[dict]:
    rows = []
    for emp in employees:
        days = emp.get("days", {})
        original = emp.get("original_days", {})
        cells = []
        for iso in dates:
            value = days.get(iso) or ""
            schedule = emp.get("schedule", {}).get(iso)
            worked = emp.get("worked_seconds", {}).get(iso)
            tooltip = " · ".join(part for part in (schedule, format_seconds(worked) if worked is not None else None) if part)
            cells.append({
                "date": iso,
                "value": value,
                "edited": value != (original.get(iso) or ""),
                "calculated": emp.get("calculated_days", {}).get(iso) or "",
                "tooltip": tooltip or "No SAP data",
            })
        badge = None
        if (emp.get("emp_status") or "").lower() == "withdrawn":
            badge = "Withdrawn" + (f" · LWD {fmt_date(emp['lwd'])}" if emp.get("lwd") else "")
        rows.append({
            "employee_id": emp["employee_id"],
            "employee_name": emp.get("employee_name", ""),
            "manager_name": emp.get("manager_name") or "",
            "department": emp.get("department") or "",
            "badge": badge,
            "cells": cells,
            "totals": compute_totals(days),
        })
    return rows


TOTAL_HEADERS = TOTAL_CODES + ["Total"]
