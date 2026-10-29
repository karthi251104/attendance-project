"""HR screens: cycles list, upload, review, all employees, change history, downloads, emails."""

import csv
import io
import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from app.auth.session import CurrentUser
from app.core.editing import read_only_reason
from app.core.processing import CONTENT_TYPES, process_upload
from app.core.rules import Thresholds
from app.core.timeutil import end_of_day_utc, now_utc, to_local
from app.core.workbook_reader import WorkbookError
from app.core.workbook_writer import WriterError, build_updated_workbook
from app.email.service import send_manager_emails
from app.routes.grid_view import TOTAL_HEADERS, date_headers, grid_rows
from app.web import Services, csrf_protect, load_cycle, render, require_hr, services

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hr")


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def _deadline(value: str | None, svc: Services):
    if not value:
        return None
    try:
        return end_of_day_utc(date.fromisoformat(value), svc.settings.timezone)
    except ValueError:
        raise WorkbookError("The correction deadline is not a valid date.") from None


def csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        # Prefix values that Excel would run as formulas (CSV injection).
        writer.writerow(["'" + str(v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row])
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- cycles and upload ----------

@router.get("")
def cycles_list(request: Request, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    return render(request, "hr_cycles.html", {"cycles": svc.repo.list_cycles()})


@router.get("/upload")
def upload_form(request: Request, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    return render(request, "hr_upload.html", {"max_mb": svc.settings.max_upload_mb})


@router.post("/upload", dependencies=[Depends(csrf_protect)])
async def upload(request: Request, file: UploadFile = File(...), deadline: str | None = Form(None),
                 user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    limit = svc.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        cycle_id = await run_in_threadpool(
            process_upload, filename=file.filename or "", data=data, uploaded_by=user.email,
            correction_deadline=_deadline(deadline, svc), repo=svc.repo, files=svc.files, settings=svc.settings)
    except WorkbookError as exc:
        log.info("Upload rejected: %s", exc.__class__.__name__)
        return render(request, "hr_upload.html", {"max_mb": svc.settings.max_upload_mb, "error": str(exc)}, status_code=400)
    flash(request, "Workbook uploaded and processed.")
    return RedirectResponse(f"/hr/cycles/{cycle_id}", status_code=303)


# ---------- review ----------

@router.get("/cycles/{cycle_id}")
def review(request: Request, cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    emails = svc.repo.latest_email_by_manager(cycle_id)
    changes = svc.repo.change_counts_by_manager(cycle_id)
    managers = [{**m, "email_log": emails.get(m["manager_email"]), "changes": changes.get(m["manager_email"], 0)}
                for m in svc.repo.managers(cycle_id)]
    exceptions = svc.repo.list_exceptions(cycle_id)
    severity_order = {"error": 0, "warning": 1, "info": 2}
    exceptions.sort(key=lambda e: severity_order.get(e["severity"], 3))
    deadline = cycle.get("correction_deadline")
    return render(request, "hr_review.html", {
        "cycle": cycle, "managers": managers, "exceptions": exceptions[:500], "exception_total": len(exceptions),
        "deadline_value": to_local(deadline, svc.settings.timezone).date().isoformat() if deadline else "",
        "manager_reason": read_only_reason(cycle, "manager", svc.settings),
    })


@router.get("/cycles/{cycle_id}/exceptions.csv")
def exceptions_csv(cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    rows = [[e["severity"], e["type"], e.get("employee_id") or "", e.get("employee_name") or "", e.get("date") or "", e["message"]]
            for e in svc.repo.list_exceptions(cycle_id)]
    return csv_response(f"exceptions_{cycle_id}.csv", ["Severity", "Type", "Employee ID", "Employee Name", "Date", "Message"], rows)


@router.post("/cycles/{cycle_id}/status", dependencies=[Depends(csrf_protect)])
def set_status(request: Request, cycle_id: str, action: str = Form(...),
               user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    if action in ("lock", "unlock"):
        svc.repo.set_cycle_fields(cycle_id, {"status": "locked" if action == "lock" else "open"})
        flash(request, "Cycle locked. Managers can no longer edit." if action == "lock" else "Cycle unlocked.")
    elif action == "activate":
        svc.repo.make_active(cycle_id)
        flash(request, "This upload is now the active version for its period.")
    log.info("Cycle %s: %s", cycle_id, action)
    return RedirectResponse(f"/hr/cycles/{cycle_id}", status_code=303)


@router.post("/cycles/{cycle_id}/deadline", dependencies=[Depends(csrf_protect)])
def set_deadline(request: Request, cycle_id: str, deadline: str | None = Form(None),
                 user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    load_cycle(svc, cycle_id)
    try:
        svc.repo.set_cycle_fields(cycle_id, {"correction_deadline": _deadline(deadline, svc)})
        flash(request, "Deadline updated." if deadline else "Deadline removed.")
    except WorkbookError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(f"/hr/cycles/{cycle_id}", status_code=303)


# ---------- emails ----------

@router.post("/cycles/{cycle_id}/emails", dependencies=[Depends(csrf_protect)])
def send_emails(request: Request, cycle_id: str, background: BackgroundTasks, manager_email: str | None = Form(None),
                user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    only = manager_email.strip().lower() if manager_email else None
    background.add_task(send_manager_emails, svc.repo, svc.email_sender, cycle, svc.settings, only)
    flash(request, "Email is being sent. Refresh this page to see the status." if only
          else "Emails are being sent to all managers. Refresh this page to see the status.")
    return RedirectResponse(f"/hr/cycles/{cycle_id}#managers", status_code=303)


# ---------- download ----------

@router.post("/cycles/{cycle_id}/download", dependencies=[Depends(csrf_protect)])
async def download(request: Request, cycle_id: str, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    employees = svc.repo.list_employees(cycle_id)
    try:
        original = svc.files.load(cycle["original_file_blob"])
        data, report = await run_in_threadpool(build_updated_workbook, original, cycle["sheet_layout"], employees,
                                               Thresholds.from_settings(svc.settings))
    except WriterError as exc:
        flash(request, f"The workbook could not be built: {exc}", "error")
        return RedirectResponse(f"/hr/cycles/{cycle_id}", status_code=303)
    extension = cycle.get("file_extension", ".xlsm")
    stamp = to_local(now_utc(), svc.settings.timezone).strftime("%Y%m%d_%H%M")
    period = cycle["name"].replace(" ", "").replace("-", "_")
    log.info("Download: cycle=%s day_cells=%d", cycle_id, report.day_cells)
    return Response(data, media_type=CONTENT_TYPES[extension], headers={
        "Content-Disposition": f'attachment; filename="Attendance_{period}_updated_{stamp}{extension}"'})


# ---------- all employees and change history ----------

@router.get("/cycles/{cycle_id}/employees")
def all_employees(request: Request, cycle_id: str, manager: str | None = None, department: str | None = None,
                  user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    employees = svc.repo.list_employees(cycle_id, manager_email=manager or None, department=department or None)
    return render(request, "grid.html", {
        "cycle": cycle, "dates": date_headers(cycle["dates"]), "rows": grid_rows(employees, cycle["dates"]),
        "total_headers": TOTAL_HEADERS, "statuses": svc.settings.allowed_status_list,
        "read_only_reason": read_only_reason(cycle, "hr", svc.settings), "hr_view": True,
        "managers": svc.repo.managers(cycle_id), "departments": svc.repo.departments(cycle_id),
        "filter_manager": manager or "", "filter_department": department or "",
    })


def _changes(svc: Services, cycle_id: str, employee: str | None, changed_by: str | None, manager: str | None) -> list[dict]:
    return svc.repo.list_changes(cycle_id, employee_id=(employee or "").strip() or None,
                                 changed_by=(changed_by or "").strip().lower() or None, manager_email=manager or None)


@router.get("/cycles/{cycle_id}/changes")
def change_history(request: Request, cycle_id: str, employee: str | None = None, changed_by: str | None = None,
                   manager: str | None = None, user: CurrentUser = Depends(require_hr), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    names = {e["employee_id"]: e.get("employee_name", "") for e in svc.repo.list_employees(cycle_id)}
    return render(request, "hr_changes.html", {
        "cycle": cycle, "changes": _changes(svc, cycle_id, employee, changed_by, manager), "names": names,
        "managers": svc.repo.managers(cycle_id),
        "filters": {"employee": employee or "", "changed_by": changed_by or "", "manager": manager or ""},
    })


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
