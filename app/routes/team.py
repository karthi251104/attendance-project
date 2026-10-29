"""Manager screens: the home redirect and the team grid."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.auth.session import CurrentUser
from app.core.editing import read_only_reason
from app.routes.grid_view import TOTAL_HEADERS, date_headers, grid_rows
from app.web import Services, cycle_role, load_cycle, render, require_user, services

router = APIRouter()


@router.get("/")
def home(request: Request, user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    if user.is_hr:
        return RedirectResponse("/hr", status_code=303)
    cycles = svc.repo.active_cycles_for_manager(user.email)
    if len(cycles) == 1:
        return RedirectResponse(f"/team/{cycles[0]['id']}", status_code=303)
    return render(request, "team_home.html", {"cycles": cycles})


@router.get("/team/{cycle_id}")
def team_grid(request: Request, cycle_id: str, user: CurrentUser = Depends(require_user), svc: Services = Depends(services)):
    cycle = load_cycle(svc, cycle_id)
    role = cycle_role(svc, cycle, user)
    # HR opening a manager link sees everyone; a manager sees only their own team.
    employees = svc.repo.list_employees(cycle_id, manager_email=None if role == "hr" else user.email)
    return render(request, "grid.html", {
        "cycle": cycle, "dates": date_headers(cycle["dates"]), "rows": grid_rows(employees, cycle["dates"]),
        "total_headers": TOTAL_HEADERS, "statuses": svc.settings.allowed_status_list,
        "read_only_reason": read_only_reason(cycle, role, svc.settings), "hr_view": False,
    })
