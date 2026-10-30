"""The JSON API for attendance cycles.

HR only: list, upload, managers, exceptions, change history, emails, lock / deadline, download.
HR and the cycle's managers: the cycle, its employees (a manager gets only their own team), saving edits.
Every check runs here on the server; the React app only mirrors them.
"""

import csv
import io
import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth.session import CurrentUser
from app.core.editing import Change, EditError, save_changes
from app.core.processing import CONTENT_TYPES, process_upload
from app.core.rules import Thresholds
from app.core.timeutil import end_of_day_utc, now_utc, to_local
from app.core.workbook_reader import WorkbookError
from app.core.workbook_writer import WriterError, build_updated_workbook
from app.email.service import send_manager_emails
from app.views import change_json, cycle_json, employee_json, exception_json, iso
from app.web import Services, csrf_protect, cycle_role, load_cycle, require_hr, require_user, services

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        # Prefix values that Excel would run as formulas (CSV injection).
        writer.writerow(["'" + str(v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def parse_deadline(value: str | None, svc: Services):
    if not value:
        return None
    try:
        return end_of_day_utc(date.fromisoformat(value), svc.settings.timezone)
    except ValueError:
        raise WorkbookError("The correction deadline is not a valid date.") from None


# ---------- cycles ----------

@router.get("/cycles")
def list_cycles(user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    return {"cycles": [cycle_json(c, svc.settings) for c in svc.repo.list_cycles()]}


@router.get("/my-cycles")
def my_cycles(user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    """The active cycles where the signed-in user is a manager."""
    return {"cycles": [cycle_json(c, svc.settings) for c in svc.repo.active_cycles_for_manager(user.email)]}


@router.post("/cycles", dependencies=[Depends(csrf_protect)])
async def upload(file: UploadFile = File(...), deadline: str | None = Form(None),
                 user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    limit = svc.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        cycle_id = await run_in_threadpool(
            process_upload, filename=file.filename or "", data=data, uploaded_by=user.email,
            correction_deadline=parse_deadline(deadline, svc), repo=svc.repo, files=svc.files, settings=svc.settings)
    except WorkbookError as exc:
        log.info("Upload rejected: %s", exc.__class__.__name__)
        return error(str(exc))
    return {"id": cycle_id}


@router.get("/cycles/{cycle_id}")
def get_cycle(cycle_id: str, user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    return cycle_json(cycle, svc.settings, role=cycle_role(svc, cycle, user))


class StatusIn(BaseModel):
    action: Literal["lock", "unlock", "activate"]


@router.post("/cycles/{cycle_id}/status", dependencies=[Depends(csrf_protect)])
def set_status(cycle_id: str, body: StatusIn, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    if body.action == "activate":
        svc.repo.make_active(cycle_id)
    else:
        svc.repo.set_cycle_fields(cycle_id, {"status": "locked" if body.action == "lock" else "open"})
    log.info("Cycle %s: %s", cycle_id, body.action)
    return cycle_json(load_cycle(svc, cycle["id"]), svc.settings, role="hr")


class DeadlineIn(BaseModel):
    deadline: str | None = Field(default=None, max_length=10)  # yyyy-mm-dd, or null to remove


@router.put("/cycles/{cycle_id}/deadline", dependencies=[Depends(csrf_protect)])
def set_deadline(cycle_id: str, body: DeadlineIn, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    try:
        svc.repo.set_cycle_fields(cycle_id, {"correction_deadline": parse_deadline(body.deadline, svc)})
    except WorkbookError as exc:
        return error(str(exc))
    return cycle_json(load_cycle(svc, cycle_id), svc.settings, role="hr")


# ---------- review: managers, exceptions ----------

@router.get("/cycles/{cycle_id}/managers")
def managers(cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    emails = svc.repo.latest_email_by_manager(cycle_id)
    changes = svc.repo.change_counts_by_manager(cycle_id)
    rows = []
    for m in svc.repo.managers(cycle_id):
        log_entry = emails.get(m["manager_email"])
        rows.append({
            **m,
            "changes": changes.get(m["manager_email"], 0),
            "email_status": log_entry["status"] if log_entry else None,
            "email_error": log_entry.get("error") if log_entry else None,
            "email_sent_at": iso(log_entry["sent_at"]) if log_entry else None,
        })
    return {"managers": rows, "departments": svc.repo.departments(cycle_id)}


@router.get("/cycles/{cycle_id}/exceptions")
def exceptions(cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    items = sorted(svc.repo.list_exceptions(cycle_id), key=lambda e: SEVERITY_ORDER.get(e["severity"], 3))
    return {"exceptions": [exception_json(e) for e in items]}


@router.get("/cycles/{cycle_id}/exceptions.csv")
def exceptions_csv(cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    rows = [[e["severity"], e["type"], e.get("employee_id") or "", e.get("employee_name") or "", e.get("date") or "", e["message"]]
            for e in svc.repo.list_exceptions(cycle_id)]
    return csv_response(f"exceptions_{cycle_id}.csv", ["Severity", "Type", "Employee ID", "Employee Name", "Date", "Message"], rows)


# ---------- employees and edits ----------

@router.get("/cycles/{cycle_id}/employees")
def employees(cycle_id: str, manager: str | None = None, department: str | None = None,
              user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    role = cycle_role(svc, cycle, user)
    if role == "hr":
        rows = svc.repo.list_employees(cycle_id, manager_email=manager or None, department=department or None)
    else:
        rows = svc.repo.list_employees(cycle_id, manager_email=user.email)  # filters are ignored for managers
    return {"employees": [employee_json(e, include_manager=role == "hr") for e in rows]}


class ChangeIn(BaseModel):
    employee_id: str = Field(max_length=20)
    date: str = Field(max_length=10)
    value: str = Field(max_length=10)
    old_value: str | None = Field(default=None, max_length=10)


class SaveIn(BaseModel):
    changes: list[ChangeIn] = Field(max_length=10_000)
    comment: str | None = Field(default=None, max_length=500)


@router.post("/cycles/{cycle_id}/changes", dependencies=[Depends(csrf_protect)])
def save(cycle_id: str, body: SaveIn, user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    role = cycle_role(svc, cycle, user)
    changes = [Change(employee_id=c.employee_id, date=c.date, value=c.value,
                      old_value=c.old_value or None, comment=body.comment) for c in body.changes]
    try:
        result = save_changes(svc.repo, cycle, user.email, role, changes, svc.settings)
    except EditError as exc:
        return error(exc.message, exc.status_code)
    return {"saved": result.saved, "unchanged": result.unchanged, "conflicts": result.conflicts}


# ---------- change history ----------

def _changes(svc: Services, cycle_id: str, employee: str | None, changed_by: str | None, manager: str | None) -> list[dict]:
    return svc.repo.list_changes(cycle_id, employee_id=(employee or "").strip() or None,
                                 changed_by=(changed_by or "").strip().lower() or None, manager_email=manager or None)


@router.get("/cycles/{cycle_id}/changes")
def change_history(cycle_id: str, employee: str | None = None, changed_by: str | None = None, manager: str | None = None,
                   user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    names = {e["employee_id"]: e.get("employee_name", "") for e in svc.repo.list_employees(cycle_id)}
    return {"changes": [change_json(c, names) for c in _changes(svc, cycle_id, employee, changed_by, manager)]}


@router.get("/cycles/{cycle_id}/changes.csv")
def change_history_csv(cycle_id: str, employee: str | None = None, changed_by: str | None = None, manager: str | None = None,
                       user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    names = {e["employee_id"]: e.get("employee_name", "") for e in svc.repo.list_employees(cycle_id)}
    tz = svc.settings.timezone
    rows = [[to_local(c["changed_at"], tz).strftime("%d/%m/%Y %H:%M"), c["employee_id"], names.get(c["employee_id"], ""),
             date.fromisoformat(c["date"]).strftime("%d/%m/%Y"), c.get("old_value") or "", c["new_value"],
             c["changed_by_email"], c["changed_by_role"], c.get("comment") or ""]
            for c in _changes(svc, cycle_id, employee, changed_by, manager)]
    return csv_response(f"changes_{cycle_id}.csv", ["Changed at", "Employee ID", "Employee Name", "Date", "Old", "New",
                                                    "Changed by", "Role", "Comment"], rows)


# ---------- emails ----------

class EmailsIn(BaseModel):
    manager_email: str | None = Field(default=None, max_length=200)  # None = every manager


@router.post("/cycles/{cycle_id}/emails", dependencies=[Depends(csrf_protect)])
def send_emails(cycle_id: str, body: EmailsIn, background: BackgroundTasks,
                user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    only = body.manager_email.strip().lower() if body.manager_email else None
    background.add_task(send_manager_emails, svc.repo, svc.email_sender, cycle, svc.settings, only)
    return {"queued": 1 if only else len(svc.repo.managers(cycle_id))}


# ---------- download ----------

@router.get("/cycles/{cycle_id}/download")
async def download(cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    employees = svc.repo.list_employees(cycle_id)
    try:
        original = svc.files.load(cycle["original_file_blob"])
        data, report = await run_in_threadpool(build_updated_workbook, original, cycle["sheet_layout"], employees,
                                               Thresholds.from_settings(svc.settings))
    except WriterError as exc:
        return error(f"The workbook could not be built: {exc}", 500)
    extension = cycle.get("file_extension", ".xlsm")
    stamp = to_local(now_utc(), svc.settings.timezone).strftime("%Y%m%d_%H%M")
    period = cycle["name"].replace(" ", "").replace("-", "_")
    log.info("Download: cycle=%s day_cells=%d", cycle_id, report.day_cells)
    return Response(data, media_type=CONTENT_TYPES[extension], headers={
        "Content-Disposition": f'attachment; filename="Attendance_{period}_updated_{stamp}{extension}"'})
