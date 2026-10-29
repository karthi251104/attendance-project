"""MongoDB connection and indexes.

Production uses Azure Cosmos DB for MongoDB. Its connection string must include
`retrywrites=false`. Local development can use MongoDB in Docker
(docker-compose.yml) or, for a quick try without Docker, MONGODB_URI=mongomock://,
an in-memory database that is lost on restart. It is refused in production.
"""

import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from app.config import Settings

log = logging.getLogger(__name__)


def connect(settings: Settings) -> Database:
    if settings.mongodb_uri.startswith("mongomock://"):
        import mongomock  # dev/test only; not in requirements.txt

        log.warning("Using in-memory mongomock database; data is lost on restart")
        return mongomock.MongoClient(tz_aware=True)[settings.mongodb_db]

    from pymongo import MongoClient

    client = MongoClient(settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=10_000, appname="attendance-portal")
    return client[settings.mongodb_db]


def ensure_indexes(db: Database) -> None:
    db.cycles.create_index([("uploaded_at", DESCENDING)])
    db.employees.create_index([("cycle_id", ASCENDING), ("employee_id", ASCENDING)], unique=True)
    db.employees.create_index([("cycle_id", ASCENDING), ("manager_email", ASCENDING)])
    db.exceptions.create_index([("cycle_id", ASCENDING)])
    db.changes.create_index([("cycle_id", ASCENDING), ("changed_at", DESCENDING)])
    db.email_log.create_index([("cycle_id", ASCENDING), ("manager_email", ASCENDING)])


def ping(db: Database) -> bool:
    try:
        db.command("ping")
        return True
    except Exception:  # health check: any failure means "not healthy"
        log.exception("Database ping failed")
        return False
