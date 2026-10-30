# One image with the React app and the API, served together on port 8000.
#   docker build -t attendance-portal .
#   docker run -p 8000:8000 --env-file .env attendance-portal

# ---- 1. Build the React app ----
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    FRONTEND_DIST=/app/frontend/dist
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY gunicorn.conf.py ./
COPY app ./app
COPY --from=frontend /frontend/dist ./frontend/dist

# Never run as root. The home folder is where gunicorn keeps its control socket.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin portal
USER portal

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
