"""Upload pipeline: validate -> read -> calculate -> grid -> exceptions -> save.

Called by the HR upload route. Everything that can fail on bad input fails
before anything is written to the database, and a failure while saving rolls
back the partial cycle, so HR never sees a half-imported upload.
"""

import logging
import zipfile
from dataclasses import asdict
from datetime import date, datetime
from io import BytesIO
from pathlib import PurePath

from app.config import Settings
from app.core.grid import CycleData, build_cycle_data
from app.core.timeutil import date_to_datetime, now_utc
from app.core.workbook_reader import WorkbookError, read_workbook
from app.db.file_store import FileStore
from app.db.repositories import Repository

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".xlsm", ".xlsx"}
CONTENT_TYPES = {
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def validate_upload(filename: str, data: bytes, settings: Settings) -> str:
    """Check name, size and that the file is really an Excel zip. Returns the lower-case extension."""
    extension = PurePath(filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise WorkbookError("Please upload an Excel workbook (.xlsm or .xlsx).")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise WorkbookError(f"The file is larger than {settings.max_upload_mb} MB.")
    if not data or not zipfile.is_zipfile(BytesIO(data)):
        raise WorkbookError("This file could not be opened as an Excel workbook (.xlsm or .xlsx).")
    return extension


def cycle_name(start: date, end: date) -> str:
    return f"{start:%d %b %Y} - {end:%d %b %Y}"


def layout_to_doc(cycle: CycleData) -> dict:
    layout = cycle.layout
    return {
        "header_row": layout.header_row,
        "date_columns": layout.date_columns,
        "employee_rows": layout.employee_rows,
        "total_columns": [asdict(col) for col in layout.total_columns],
        "sap_columns": layout.sap_columns,
        "sap_last_row": layout.sap_last_row,
    }


def process_upload(
    *,
    filename: str,
    data: bytes,
    uploaded_by: str,
    correction_deadline: datetime | None,
    repo: Repository,
    files: FileStore,
    settings: Settings,
) -> str:
    """Import one workbook as a new cycle and return its id. Raises WorkbookError for bad files."""
    extension = validate_upload(filename, data, settings)
    cycle = build_cycle_data(read_workbook(data), settings)

    file_id = files.save(data, filename=filename, content_type=CONTENT_TYPES[extension])
    cycle_id = None
    try:
        cycle_id = repo.insert_cycle({
            "name": cycle_name(cycle.dates[0], cycle.dates[-1]),
            "start_date": date_to_datetime(cycle.dates[0]),
            "end_date": date_to_datetime(cycle.dates[-1]),
            "dates": [d.isoformat() for d in cycle.dates],
            "status": "open",
            "active": False,
            "correction_deadline": correction_deadline,
            "uploaded_by": uploaded_by,
            "uploaded_at": now_utc(),
            "original_filename": filename,
            "file_extension": extension,
            "original_file_blob": file_id,
            "sheet_layout": layout_to_doc(cycle),
            "stats": {
                "employees": len(cycle.employees),
                "managers": len(cycle.manager_emails),
                "exceptions": len(cycle.exceptions),
                "filled_from_sap": cycle.filled_days,
            },
        })
        repo.insert_employees([_employee_doc(cycle_id, emp) for emp in cycle.employees])
        repo.insert_exceptions([{"cycle_id": cycle_id, **asdict(issue)} for issue in cycle.exceptions])
        repo.make_active(cycle_id)  # the newest upload becomes the active version of its period
    except Exception:
        log.exception("Upload failed while saving; rolling back")
        if cycle_id:
            repo.delete_cycle(cycle_id)
        files.delete(file_id)
        raise

    log.info("Upload processed: cycle=%s employees=%d exceptions=%d",
             cycle_id, len(cycle.employees), len(cycle.exceptions))
    return cycle_id


def _employee_doc(cycle_id: str, emp) -> dict:
    doc = asdict(emp)
    doc["cycle_id"] = cycle_id
    doc["doj"] = date_to_datetime(emp.doj)
    doc["lwd"] = date_to_datetime(emp.lwd)
    return doc
