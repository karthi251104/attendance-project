"""Gunicorn settings for the container (see Dockerfile). Every value can be overridden with an env var."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn_worker.UvicornWorker"
timeout = 300  # large uploads and downloads take a few seconds; never cut them off
graceful_timeout = 30
keepalive = 5
# Azure App Service (and dev tunnels) terminate HTTPS in front of the app and add X-Forwarded-* headers.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
