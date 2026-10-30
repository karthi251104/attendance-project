"""All database reads and writes live here, so routes never build Mongo queries themselves.

`cycle_id` is stored as a string (the cycle's ObjectId in hex) in every
collection except `cycles` itself.
"""

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database


def _oid(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _with_id(doc: dict | None) -> dict | None:
    """Add a string `id` field next to Mongo's ObjectId `_id`, for the API."""
    if doc is not None:
        doc["id"] = str(doc["_id"])
    return doc


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------- cycles ----------

    def insert_cycle(self, doc: dict) -> str:
        return str(self.db.cycles.insert_one(doc).inserted_id)

    def delete_cycle(self, cycle_id: str) -> None:
        """Used only to roll back a failed upload."""
        oid = _oid(cycle_id)
        if oid:
            self.db.cycles.delete_one({"_id": oid})
        for name in ("employees", "exceptions"):
            self.db[name].delete_many({"cycle_id": cycle_id})

    def get_cycle(self, cycle_id: str) -> dict | None:
        oid = _oid(cycle_id)
        return _with_id(self.db.cycles.find_one({"_id": oid})) if oid else None

    def list_cycles(self) -> list[dict]:
        return [_with_id(c) for c in self.db.cycles.find({}, {"sheet_layout": 0}).sort("uploaded_at", DESCENDING)]

    def set_cycle_fields(self, cycle_id: str, fields: dict) -> None:
        self.db.cycles.update_one({"_id": _oid(cycle_id)}, {"$set": fields})

    def make_active(self, cycle_id: str) -> None:
        """Mark one upload as the active version of its period; other versions become inactive."""
        cycle = self.get_cycle(cycle_id)
        if cycle is None:
            return
        self.db.cycles.update_many(
            {"start_date": cycle["start_date"], "end_date": cycle["end_date"], "_id": {"$ne": cycle["_id"]}},
            {"$set": {"active": False}},
        )
        self.set_cycle_fields(cycle_id, {"active": True})

    def active_cycles_for_manager(self, email: str) -> list[dict]:
        cycle_ids = self.db.employees.distinct("cycle_id", {"manager_email": email})
        oids = [oid for oid in map(_oid, cycle_ids) if oid]
        query = {"_id": {"$in": oids}, "active": True}
        return [_with_id(c) for c in self.db.cycles.find(query, {"sheet_layout": 0}).sort("start_date", DESCENDING)]

    # ---------- employees ----------

    def insert_employees(self, docs: list[dict]) -> None:
        if docs:
            self.db.employees.insert_many(docs, ordered=True)

    def list_employees(self, cycle_id: str, manager_email: str | None = None, department: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"cycle_id": cycle_id}
        if manager_email is not None:
            query["manager_email"] = manager_email
        if department:
            query["department"] = department
        return list(self.db.employees.find(query).sort([("manager_name", ASCENDING), ("employee_name", ASCENDING)]))

    def get_employee(self, cycle_id: str, employee_id: str) -> dict | None:
        return self.db.employees.find_one({"cycle_id": cycle_id, "employee_id": employee_id})

    def is_manager(self, cycle_id: str, email: str) -> bool:
        return self.db.employees.count_documents({"cycle_id": cycle_id, "manager_email": email}, limit=1) > 0

    def update_day(self, cycle_id: str, employee_id: str, iso: str, old_value: str | None, new_value: str) -> bool:
        """Set one day, only if it still holds `old_value` (protects against two people saving at once)."""
        result = self.db.employees.update_one(
            {"cycle_id": cycle_id, "employee_id": employee_id, f"days.{iso}": old_value},
            {"$set": {f"days.{iso}": new_value}},
        )
        return result.modified_count == 1

    def managers(self, cycle_id: str) -> list[dict]:
        """One row per manager email: name and team size."""
        teams: dict[str, dict] = {}
        for emp in self.db.employees.find({"cycle_id": cycle_id}, {"manager_email": 1, "manager_name": 1}):
            key = emp.get("manager_email") or ""
            team = teams.setdefault(key, {"manager_email": key, "manager_name": emp.get("manager_name") or "", "team_size": 0})
            team["team_size"] += 1
        return sorted(teams.values(), key=lambda t: (t["manager_name"].lower(), t["manager_email"]))

    def departments(self, cycle_id: str) -> list[str]:
        return sorted(d for d in self.db.employees.distinct("department", {"cycle_id": cycle_id}) if d)

    # ---------- exceptions ----------

    def insert_exceptions(self, docs: list[dict]) -> None:
        if docs:
            self.db.exceptions.insert_many(docs, ordered=False)

    def list_exceptions(self, cycle_id: str) -> list[dict]:
        return list(self.db.exceptions.find({"cycle_id": cycle_id}).sort("_id", ASCENDING))

    # ---------- changes (append-only audit log) ----------

    def insert_change(self, doc: dict) -> None:
        self.db.changes.insert_one(doc)

    def list_changes(self, cycle_id: str, employee_id: str | None = None, changed_by: str | None = None,
                     manager_email: str | None = None) -> list[dict]:
        query: dict[str, Any] = {"cycle_id": cycle_id}
        if employee_id:
            query["employee_id"] = employee_id
        if changed_by:
            query["changed_by_email"] = changed_by
        if manager_email:
            query["manager_email"] = manager_email
        return list(self.db.changes.find(query).sort("changed_at", DESCENDING))

    def change_counts_by_manager(self, cycle_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for change in self.db.changes.find({"cycle_id": cycle_id}, {"manager_email": 1}):
            key = change.get("manager_email") or ""
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ---------- portal accounts (AUTH_MODE=password) ----------

    def get_account(self, email: str) -> dict | None:
        return self.db.accounts.find_one({"email": email})

    def list_accounts(self) -> list[dict]:
        return list(self.db.accounts.find({}, {"password_hash": 0}).sort("email", ASCENDING))

    def insert_account(self, doc: dict) -> None:
        self.db.accounts.insert_one(doc)

    def set_account_fields(self, email: str, fields: dict) -> bool:
        return self.db.accounts.update_one({"email": email}, {"$set": fields}).matched_count == 1

    def active_managers(self) -> dict[str, str]:
        """Manager email -> name, for every manager in an active cycle."""
        active = [str(c["_id"]) for c in self.db.cycles.find({"active": True}, {"_id": 1})]
        managers: dict[str, str] = {}
        for emp in self.db.employees.find({"cycle_id": {"$in": active}}, {"manager_email": 1, "manager_name": 1}):
            if emp.get("manager_email"):
                managers.setdefault(emp["manager_email"], emp.get("manager_name") or "")
        return managers

    # ---------- email log ----------

    def insert_email_log(self, doc: dict) -> None:
        self.db.email_log.insert_one(doc)

    def latest_email_by_manager(self, cycle_id: str) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for entry in self.db.email_log.find({"cycle_id": cycle_id}).sort("sent_at", ASCENDING):
            latest[entry["manager_email"]] = entry
        return latest

