"""Portal accounts for AUTH_MODE=password.

  * The login ID is the person's work email (the Manager Email in TO CONSIDER, or an HR_ADMIN_EMAILS entry).
  * HR creates logins and gives each person their generated password; anyone can change their own later.
  * HR can reset a password (a new one is generated) or disable a login.
  * 5 wrong passwords in a row lock the login for 15 minutes.
  * A reset, a password change or disabling a login ends that person's other sessions at once.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from pymongo.errors import DuplicateKeyError

from app.auth.passwords import generate_password, hash_password, password_problem, verify_password
from app.config import Settings
from app.core.timeutil import as_utc, now_utc
from app.db.repositories import Repository

log = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
WRONG_LOGIN = "The email or password is wrong."


class AccountError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class NewLogin:
    email: str
    name: str
    password: str  # shown to HR once, never stored in readable form


def normalize(email: str) -> str:
    return email.strip().lower()


def check_login(repo: Repository, email: str, password: str) -> dict:
    """Return the account for a correct email and password, or raise AccountError."""
    email = normalize(email)
    account = repo.get_account(email)
    now = now_utc()
    if account and account.get("locked_until") and as_utc(account["locked_until"]) > now:
        minutes = max(1, int((as_utc(account["locked_until"]) - now).total_seconds() // 60) + 1)
        raise AccountError(f"Too many wrong passwords. Try again in {minutes} minutes, or ask HR to reset it.", 429)
    if not verify_password(password, account.get("password_hash") if account else None) or not account:
        if account:
            attempts = account.get("failed_attempts", 0) + 1
            locked = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_FAILED_ATTEMPTS else None
            repo.set_account_fields(email, {"failed_attempts": 0 if locked else attempts, "locked_until": locked})
            if locked:
                log.warning("Login locked after %d failed attempts", attempts)
        raise AccountError(WRONG_LOGIN, 401)
    if not account.get("active", True):
        raise AccountError("This login is disabled. Please contact HR.", 403)
    repo.set_account_fields(email, {"failed_attempts": 0, "locked_until": None, "last_login_at": now})
    return account


def session_still_valid(repo: Repository, email: str, login_at: datetime) -> bool:
    """False when the login was disabled, removed or its password changed after this session started."""
    account = repo.get_account(email)
    if account is None or not account.get("active", True):
        return False
    changed = account.get("password_changed_at")
    return changed is None or as_utc(changed) <= login_at


def _new_account(repo: Repository, email: str, name: str, created_by: str, password: str) -> None:
    now = now_utc()
    repo.insert_account({
        "email": email, "name": name, "password_hash": hash_password(password), "active": True,
        "created_at": now, "created_by": created_by, "password_changed_at": now,
        "failed_attempts": 0, "locked_until": None, "last_login_at": None,
    })


def ensure_hr_accounts(repo: Repository, settings: Settings) -> int:
    """At startup: give every HR email without a login the INITIAL_HR_PASSWORD. Returns how many were created."""
    if settings.auth_mode != "password" or not settings.initial_hr_password:
        return 0
    created = 0
    for email in settings.hr_admin_email_list:
        if repo.get_account(email) is None:
            try:
                _new_account(repo, email, email.split("@")[0], "startup", settings.initial_hr_password)
                created += 1
            except DuplicateKeyError:  # another worker created it at the same moment
                pass
    if created:
        log.info("Created %d HR login(s) with the initial password", created)
    return created


def known_people(repo: Repository, settings: Settings) -> list[dict]:
    """Everyone who can have a login: HR emails, managers in active cycles and existing accounts."""
    people: dict[str, dict] = {}
    for email in settings.hr_admin_email_list:
        people[email] = {"email": email, "name": "", "role": "hr"}
    for email, name in repo.active_managers().items():
        people.setdefault(email, {"email": email, "name": name, "role": "manager"})
    accounts = {a["email"]: a for a in repo.list_accounts()}
    for email, account in accounts.items():
        people.setdefault(email, {"email": email, "name": account.get("name", ""), "role": "manager"})
    now = now_utc()
    rows = []
    for person in people.values():
        account = accounts.get(person["email"])
        locked_until = account.get("locked_until") if account else None
        rows.append({
            **person,
            "name": person["name"] or (account or {}).get("name", ""),
            "has_login": account is not None,
            "active": bool(account and account.get("active", True)),
            "locked": bool(locked_until and as_utc(locked_until) > now),
            "last_login_at": account.get("last_login_at") if account else None,
            "password_changed_at": account.get("password_changed_at") if account else None,
        })
    return sorted(rows, key=lambda r: (r["role"] != "hr", (r["name"] or r["email"]).lower()))


def create_missing_logins(repo: Repository, settings: Settings, created_by: str) -> list[NewLogin]:
    """Create a login with a generated password for everyone in known_people() who has none."""
    created: list[NewLogin] = []
    for person in known_people(repo, settings):
        if person["has_login"]:
            continue
        password = generate_password()
        try:
            _new_account(repo, person["email"], person["name"], created_by, password)
        except DuplicateKeyError:
            continue
        created.append(NewLogin(person["email"], person["name"], password))
    log.info("Created %d login(s)", len(created))
    return created


def reset_password(repo: Repository, email: str, settings: Settings, reset_by: str) -> NewLogin:
    """A new generated password; also unlocks and re-enables the login. Creates the login if there is none."""
    email = normalize(email)
    person = next((p for p in known_people(repo, settings) if p["email"] == email), None)
    if person is None:
        raise AccountError("This person is not a manager in an active cycle and has no login.", 404)
    password = generate_password()
    if not person["has_login"]:
        _new_account(repo, email, person["name"], reset_by, password)
    else:
        repo.set_account_fields(email, {
            "password_hash": hash_password(password), "password_changed_at": now_utc(), "active": True,
            "failed_attempts": 0, "locked_until": None,
        })
    log.info("Password reset by HR")
    return NewLogin(email, person["name"], password)


def set_active(repo: Repository, email: str, active: bool) -> None:
    fields: dict = {"active": active}
    if not active:
        fields["password_changed_at"] = now_utc()  # ends the person's open sessions
    if not repo.set_account_fields(normalize(email), fields):
        raise AccountError("This person has no login.", 404)


def change_own_password(repo: Repository, email: str, current: str, new: str) -> None:
    account = repo.get_account(email)
    if account is None or not verify_password(current, account.get("password_hash")):
        raise AccountError("Your current password is wrong.", 400)
    problem = password_problem(new)
    if problem:
        raise AccountError(problem)
    if new == current:
        raise AccountError("The new password must be different from the current one.")
    repo.set_account_fields(email, {"password_hash": hash_password(new), "password_changed_at": now_utc()})
