"""Health check for App Service and Docker: 200 when the database answers."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db.mongo import ping
from app.web import Services, services

router = APIRouter()


@router.get("/healthz")
def healthz(svc: Services = Depends(services)):
    ok = ping(svc.repo.db)
    return JSONResponse({"status": "ok" if ok else "error", "database": ok}, status_code=200 if ok else 503)
