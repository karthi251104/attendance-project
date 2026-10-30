"""Serve the built React app (frontend/dist) from the same origin as the API.

  /assets/*   hashed JS/CSS/fonts from Vite, cached for a year
  /api/*      the API (never answered by this module)
  anything else -> index.html, so React Router can show /hr, /team/<id>, ... on reload.

In development the React app runs on the Vite dev server instead (it proxies /api to here).
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Attendance Portal</title>
<p>The frontend is not built. Run <code>npm run build</code> in <code>frontend/</code>, or use the Vite dev server
(<code>npm run dev</code>, http://localhost:5173).</p>"""


def mount_spa(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith(("api/", "auth/")) or path == "api":
            return JSONResponse({"error": "Not found."}, status_code=404)
        public_file = (dist / path).resolve()
        if path and public_file.is_file() and dist.resolve() in public_file.parents:
            return FileResponse(public_file)  # favicon and other files from frontend/public
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(NOT_BUILT)
