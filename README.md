# Attendance Correction Portal

HR uploads the monthly attendance workbook downloaded from SAP, with the `SAP` and `HC_Teamlease`
sheets. The portal does the job the Excel macro used to do: it builds the `TO CONSIDER` sheet from those
two sheets and calculates every day from `SAP`. It then emails each manager a link, and managers correct
their own team's days. HR downloads the finished workbook.

- **No AI.** The status rules are a direct translation of the macro formula (`app/core/rules.py`), and
  `TO CONSIDER` is built like the macro's pivot table and VLOOKUPs (`app/core/workbook_reader.py`).
- **Checked against the real October 2026 workbook.** Starting from the same SAP and HC_Teamlease sheets, the
  app's `TO CONSIDER` matches the macro's for all 96 employees (4,896 cells) and the SAP sheet matches cell
  by cell. See `DECISIONS.md`.

| Guide | For |
|---|---|
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Putting it on Azure: one setup script, then every push to `main` deploys |
| [docs/TESTING.md](docs/TESTING.md) | Testing with real Microsoft sign-in and email through a dev tunnel, and the test checklist |
| [DECISIONS.md](DECISIONS.md) | Why things are the way they are |

## Architecture

```
 Browser ──HTTPS──▶ Azure App Service (one Docker container, port 8000)
                     ├─ React app (frontend/, built by Vite, served as static files)
                     ├─ JSON API /api/*  (FastAPI, app/)
                     ├─ Sign-in /auth/*  (Microsoft Entra ID)
                     └─ /healthz
                           │
                           ├──▶ Cosmos DB for MongoDB (cycles, employees, audit log, uploaded files in GridFS)
                           └──▶ Microsoft Graph (manager emails, from one HR mailbox)
```

| Folder | What it is |
|---|---|
| `frontend/` | React 18 + TypeScript (Vite). Pages in `src/pages`, the API client in `src/api.ts`, styles in `src/styles.css` |
| `app/` | Python 3.12 FastAPI backend: `routes/` (API), `core/` (Excel reading, rules, writing), `db/`, `email/`, `auth/` |
| `tests/` | Backend tests (pytest). Frontend tests sit next to the code (`*.test.ts[x]`, vitest) |
| `scripts/side_by_side.py` | Compares the app with the macro on a real workbook (local only) |
| `Dockerfile`, `gunicorn.conf.py` | The production image: builds the React app, then runs the API with gunicorn |
| `.github/workflows/` | `ci.yml` (tests, build, Docker smoke test) and `deploy.yml` (to Azure on every push to `main`) |
| `infra/azure-setup.sh` | Creates everything in Azure once |

## How TO CONSIDER is built

| Column | Comes from | Macro formula |
|---|---|---|
| A Employee ID, B Employee Name | one row per employee in `SAP`, sorted by Employee ID | pivot table |
| C DOJ | `HC_Teamlease` Joining Date | `VLOOKUP(A,HC_Teamlease!A:G,7,0)` |
| D Emp Status | Employment Status | `VLOOKUP(A,…,4,0)` |
| E LWD | Relieving Date | `VLOOKUP(A,…,12,0)` |
| F Department | Department | `VLOOKUP(A,…,23,0)` |
| G Parent Org | Parent Org Unit | `VLOOKUP(A,…,25,0)` |
| H Emp Email ID | Email ID | `VLOOKUP(A,…,42,0)` |
| I Manager ID | Rep. Mgr Emp No. | `VLOOKUP(A,…,43,0)` |
| J Manager Name | Rep. Manager Name | `VLOOKUP(A,…,44,0)` |
| K Manager Email | Email ID of the **manager's** row | `VLOOKUP(I,…,42,0)` |
| L Manager Status | Employment Status of the manager's row | `VLOOKUP(I,…,4,0)` |
| M onwards | one column per SAP date: P / A / OFF / PH | `Remarks` 1–4 replaced by the codes |
| after the dates | Present, Absent, Weekly Off, Comp Off, Maternity Leave (ML), Maternity Leave (PL), PH, Total, Remarks | `COUNTIF` / `SUM` |

- HC_Teamlease columns are found by **header name**, so a moved column does not break the lookup.
- The values are written, not the VLOOKUP formulas: the raw SAP export can hold IDs as text, which makes
  Excel's VLOOKUP fail until someone runs *Text to Columns*. The app matches text and numeric IDs.
- An employee or manager missing from HC_Teamlease gets `#N/A`, as VLOOKUP would show, and is listed in the
  exceptions. An empty HC_Teamlease cell stays empty (VLOOKUP would show `0`).
- The old sheet's column D (a lookup into another workbook) is not built.

| Rule | Detail |
|---|---|
| 5DAG / 6DAG | P if worked ≥ 5:00:00, else A |
| OFF | P if worked ≥ 8:00:00, else OFF |
| PH | P if worked ≥ 5:00:00, else PH |
| other schedule | `?`, reported as an exception and never written to the workbook |

## How it works

1. **Upload** (HR): `.xlsm` / `.xlsx`, up to 25 MB, with an optional correction deadline. The file must have
   `SAP` and `HC_Teamlease` and **no** `TO CONSIDER` sheet (upload it before running the macro).
2. **Review** (HR): summary, exceptions (CSV download), managers with email status and number of changes.
3. **Email**: one email per manager with the team size and a link. No employee data goes in the email.
4. **Correct** (manager): the manager signs in with their Microsoft work account and sees only employees whose
   `Manager Email` is theirs. They click a day, pick the status and press **Save**. Every change goes into an
   append-only audit log.
5. **Download** (HR): the original file with the macro's work done:
   - a new `TO CONSIDER` sheet (first tab) with the current day values and the `COUNTIF`/`SUM` formulas,
   - empty SAP `Remarks` / `Remarks Code` cells get the macro's formulas.

   The VBA project, `HC_Teamlease` and every other SAP cell stay byte-for-byte identical.
6. **Lock** (HR) when corrections are closed.

## Local development

Requires Python 3.11+ and Node.js 20+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip ...
cp .env.example .env                              # set HR_ADMIN_EMAILS to the email you will sign in with
cd frontend && npm ci && cd ..
```

Run the API and the React dev server in two terminals:

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Open http://localhost:5173. Vite forwards `/api` and `/auth` to the API, so it behaves like production.
With `DEV_LOGIN=true` you sign in with any email (no password): an email in `HR_ADMIN_EMAILS` gets the HR
screens, a `Manager Email` from the workbook gets that manager's team. Emails are printed in the API terminal
(`EMAIL_MODE=console`).

To run exactly what production runs (needs Docker): `docker compose up --build`, then http://localhost:8000.

Database: `MONGODB_URI=mongomock://` is an in-memory database (lost on restart, refused in production).
For a real one, `docker compose up -d mongo` and `MONGODB_URI=mongodb://localhost:27017`.

### Tests

```bash
.venv/bin/python -m pytest -q          # backend: no network, no MongoDB needed (mongomock)
cd frontend && npm run typecheck && npm test
```

The backend tests use only fake workbooks from `tests/fixtures/make_sample_workbook.py`. **Never commit real
workbooks**: `.gitignore` blocks `*.xlsm`, `*.xlsx` and `*.csv`. On Windows also `pip install tzdata`.

### Checking against a real workbook (local only)

```bash
# Deletes TO CONSIDER and clears SAP Remarks, runs the app, and compares the result with the macro's file.
.venv/bin/python -m scripts.side_by_side "path/to/Attendance.xlsm" --save-dir /some/private/folder
```

Run it on a workbook where the macro **has already run**. Exit code 0 means the same result. It prints employee
IDs and dates only, never names.

## Configuration

All settings are environment variables (see `.env.example`). In Azure they are App Service
**Environment variables**; `infra/azure-setup.sh` sets most of them.

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `production` turns on strict startup checks and Secure cookies |
| `APP_BASE_URL` | `http://localhost:8000` | Public URL, used in emails and the sign-in redirect. `https://` in production |
| `SECRET_KEY` | — | Signs the session cookie. At least 32 random characters in production |
| `MONGODB_URI` / `MONGODB_DB` | local / `attendance` | Cosmos DB connection string (must contain `retrywrites=false`) |
| `DEV_LOGIN` | `false` | Email-only sign-in for development. **The app refuses to start if this is true in production** |
| `HR_ADMIN_EMAILS` | — | Comma-separated HR emails |
| `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` | — | App registration (see docs/DEPLOYMENT.md) |
| `EMAIL_MODE` | `console` | `graph` to send real email |
| `SENDER_MAILBOX` | — | Mailbox the emails are sent from |
| `EMAIL_CC` | — | Optional CC, comma-separated |
| `EMAIL_PER_SECOND` | `2` | Throttle for Graph |
| `WORKDAY_MIN_HOURS` / `OFFDAY_MIN_HOURS` / `PH_MIN_HOURS` | 5 / 8 / 5 | Rule thresholds |
| `ALLOWED_STATUSES` | `P,A,OFF,PH,CO,ML,PL` | The codes managers can choose |
| `TIMEZONE` | `Asia/Kolkata` | For deadlines and timestamps |
| `MAX_UPLOAD_MB` | `25` | |
| `SESSION_HOURS` | `8` | Sessions end this long after sign-in |
| `WEB_CONCURRENCY` | `2` | Gunicorn workers in the container |
| `FRONTEND_DIST` | `frontend/dist` | Where the built React app is (the image sets it) |

## API

All under `/api`, JSON, same-origin session cookie. Every change (POST/PUT) needs the `X-CSRF-Token` header from
`GET /api/session`.

| Method and path | Who | What |
|---|---|---|
| `GET /api/session` | anyone | Signed-in user, CSRF token, sign-in mode, allowed statuses |
| `POST /api/auth/dev-login`, `POST /api/auth/logout` | anyone | Dev sign-in (off in production), sign-out |
| `GET /auth/login`, `GET /auth/callback` | anyone | Microsoft sign-in redirects |
| `GET /api/cycles`, `POST /api/cycles` | HR | List cycles, upload a workbook (multipart: `file`, `deadline`) |
| `GET /api/my-cycles` | manager | Active cycles where the user is a manager |
| `GET /api/cycles/{id}` | HR, its managers | The cycle with the user's role and whether they can edit |
| `GET /api/cycles/{id}/employees` | HR, its managers | Grid data (managers get only their team; HR can filter `?manager=&department=`) |
| `POST /api/cycles/{id}/changes` | HR, its managers | Save edits (all or nothing, conflict-checked) |
| `GET /api/cycles/{id}/managers`, `/exceptions`, `/exceptions.csv` | HR | Review data |
| `GET /api/cycles/{id}/changes`, `/changes.csv` | HR | Audit log (`?employee=&changed_by=&manager=`) |
| `POST /api/cycles/{id}/emails` | HR | Email all managers, or one (`{"manager_email": ...}`) |
| `POST /api/cycles/{id}/status`, `PUT /api/cycles/{id}/deadline` | HR | Lock / unlock / activate, set the deadline |
| `GET /api/cycles/{id}/download` | HR | The updated workbook |

## Security notes

- Every request is checked on the server:
  - managers can read and change only their own team,
  - all edits are validated against `ALLOWED_STATUSES` and the cycle's dates,
  - a save is all-or-nothing.
- Sessions use a signed cookie: HttpOnly, SameSite=Lax, Secure in production. A session lasts at most 8 hours
  from sign-in. All changes need a CSRF token.
- Strict Content Security Policy (scripts, styles and fonts only from the app itself), X-Frame-Options DENY,
  HSTS in production. The container runs as a non-root user.
- Logs contain IDs and counts only, never employee names or emails. The one exception is the console email sender,
  which is development only.
- Two people editing the same day at once: the second save is reported as a conflict rather than silently
  overwriting the first.
- CSV exports protect against spreadsheet formula injection.
