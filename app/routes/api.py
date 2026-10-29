"""JSON API used by the grid page, plus the health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.session import CurrentUser
from app.core.editing import Change, EditError, save_changes
from app.db.mongo import ping
from app.web import Services, csrf_protect, cycle_role, load_cycle, require_user, services

router = APIRouter()


class ChangeIn(BaseModel):
    employee_id: str = Field(max_length=20)
    date: str = Field(max_length=10)
    value: str = Field(max_length=10)
    old_value: str | None = Field(default=None, max_length=10)


class SaveIn(BaseModel):
    changes: list[ChangeIn] = Field(max_length=10_000)
    comment: str | None = Field(default=None, max_length=500)


@router.post("/api/cycles/{cycle_id}/changes", dependencies=[Depends(csrf_protect)])
def save(cycle_id: str, body: SaveIn, user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    role = cycle_role(svc, cycle, user)
    changes = [Change(employee_id=c.employee_id, date=c.date, value=c.value,
                      old_value=c.old_value or None, comment=body.comment) for c in body.changes]
    try:
        result = save_changes(svc.repo, cycle, user.email, role, changes, svc.settings)
    except EditError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)
    return {"saved": result.saved, "unchanged": result.unchanged, "conflicts": result.conflicts}


@router.get("/healthz")
def healthz(request: Request, svc: Services = Depends(services)):
    ok = ping(svc.repo.db)
    return JSONResponse({"status": "ok" if ok else "error", "database": ok}, status_code=200 if ok else 503)
