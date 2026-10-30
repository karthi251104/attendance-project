# Testing before go-live

Three levels, from fastest to most real:

| Level | What it proves | Needs |
|---|---|---|
| Automated tests | Rules, Excel reading/writing, logins, permissions, API, UI logic | Nothing (runs in CI on every push) |
| Local demo | The whole flow on your PC | Python + Node |
| **Dev tunnel** | Real managers signing in from their own laptops and phones | The dev-tunnel tool and a work account |

No IT setup is needed: sign-in is by ID (work email) and password given by HR, and the portal sends no emails
(`EMAIL_MODE=console`); HR sends the link and passwords.

## Dev tunnel: let testers reach the portal on your PC

A dev tunnel gives the portal on your PC a public `https://…devtunnels.ms` address, so testers can open it from
anywhere while it still runs on your machine.

### One-time setup

1. Install the tunnel tool and sign in with your work account:

   ```powershell
   winget install Microsoft.devtunnel
   devtunnel user login
   ```

2. Create a tunnel that keeps the same address every time:

   ```powershell
   devtunnel create attendance-test --allow-anonymous
   devtunnel port create attendance-test -p 8000
   devtunnel show attendance-test
   ```

   Note the URL for port 8000, for example `https://abcd1234-8000.inc1.devtunnels.ms`.
   `--allow-anonymous` lets testers reach the page without a dev-tunnel account; the portal itself still asks
   for their ID and password.

3. Create `.env` in the repository root:

   ```ini
   ENV=development
   APP_BASE_URL=https://abcd1234-8000.inc1.devtunnels.ms
   SECRET_KEY=<any long random text>
   MONGODB_URI=mongodb://localhost:27017      # docker compose up -d mongo; or mongomock:// (lost on restart)
   AUTH_MODE=password
   DEV_LOGIN=false
   HR_ADMIN_EMAILS=you@company.com
   INITIAL_HR_PASSWORD=<a first HR password, 8+ characters>
   EMAIL_MODE=console
   ```

### Each test session

```powershell
cd frontend; npm ci; npm run build; cd ..
uvicorn app.main:app --port 8000 --proxy-headers --forwarded-allow-ips="*"
```

In a second terminal:

```powershell
devtunnel host attendance-test
```

Open the tunnel URL. The first time, the browser shows a dev-tunnel notice page; click **Continue**.
Sign in as HR with `INITIAL_HR_PASSWORD`, change it under **Password**, upload the workbook, then
**Logins → Create logins** and give each tester their ID and password. Stop with Ctrl+C in both terminals.

Tip: use a copy of the workbook where a few managers' `Email ID` cells in HC_Teamlease are the testers' emails
(or demo emails), so the testers sign in as those managers.

## Test checklist

Tick each one.

### Upload (HR)
- [ ] Upload the SAP file with SAP + HC_Teamlease: the review page shows the right employee and manager counts.
- [ ] Upload a file that still has TO CONSIDER: a clear error, nothing saved.
- [ ] Upload a file without HC_Teamlease, a `.csv`, a file over 25 MB: clear errors.
- [ ] Exceptions list the employees missing from HC_Teamlease and unknown schedules; the CSV downloads.
- [ ] Upload the same month again: the new upload becomes active, the old one shows "Older version".

### Logins
- [ ] HR signs in with `INITIAL_HR_PASSWORD` and changes it; the old password no longer works.
- [ ] **Logins → Create logins**: every manager of the upload gets a password; Copy all and Download CSV work.
      Running it again creates nothing new.
- [ ] A manager signs in with their email (any capitals) and password and sees only their team.
- [ ] A wrong password, and an email that has no login, both say "The email or password is wrong."
- [ ] Five wrong passwords lock the login for 15 minutes; HR's **Reset password** unlocks it with a new password.
- [ ] A manager changes their own password under **Password**; the old one stops working.
- [ ] HR resets a manager's password while the manager is signed in elsewhere: that session ends at once.
- [ ] HR disables a login: the person is signed out and cannot sign in; **Enable** lets them in again.
- [ ] A manager cannot open the Logins page or any HR page.
- [ ] Sign out, then press Back: the data is not shown again. After 8 hours the session ends.

### Manager corrections
- [ ] A manager sees only their own team (compare with HC_Teamlease).
- [ ] Hover a day: the SAP schedule and worked hours match the SAP sheet.
- [ ] Change a day: it turns yellow and the totals update at once. Save: "1 change saved", the cell gets a blue outline.
- [ ] Change a day back before saving: nothing left to save.
- [ ] Close the tab with unsaved changes: the browser warns.
- [ ] Two people change the same day at the same time: the second gets "changed by someone else", nothing is overwritten.
- [ ] On a phone: the grid scrolls sideways, the employee column stays fixed, saving works.

### Rules HR controls
- [ ] Set a deadline in the past: managers see read-only, HR can still edit.
- [ ] Lock the cycle: nobody can edit, including HR. Unlock: editing works again.
- [ ] Change history shows each change with who, when, old → new and the comment; the CSV matches.

### Download (HR)
- [ ] The workbook opens in Excel without a repair message, with macros intact.
- [ ] TO CONSIDER is the first tab: columns A–L, one column per date, the totals, Remarks.
- [ ] Managers' corrections are in the right cells, and the totals count them.
- [ ] SAP Remarks / Remarks Code are filled; HC_Teamlease is unchanged.
- [ ] Compare a few employees with the macro's result for the same file.

### After deploying to Azure
- [ ] `https://<app>/healthz` answers `{"status": "ok"}`.
- [ ] The whole checklist again on the Azure URL, with a few real managers.
- [ ] A push to `main` deploys by itself, and the site answers again within a minute or two.

## What is needed from you

| # | Item | Used for |
|---|---|---|
| 1 | **HR admin emails** (and a first HR password, set by you as `INITIAL_HR_PASSWORD`) | Who sees the HR screens |
| 2 | **Demo / test manager emails**, and which managers in HC_Teamlease they stand in for | Testing as managers |
| 3 | **A MongoDB** for testing: Docker on the test PC, or the Cosmos DB from the Azure setup | Data that survives restarts |
| 4 | **Azure subscription** access (Contributor) to run `infra/azure-setup.sh` | Production |
| 5 | **GitHub repo admin** to add the secrets and the `production` environment | Automatic deploys |
| 6 | Optional: a **custom domain** (e.g. `attendance.company.com`) | A friendlier address |
| 7 | Confirm the **PL** column name: the sheet calls it "Maternity Leave"; is it paternity or privilege leave? | Labels in the sheet and the portal |
| 8 | Optional, later: Microsoft sign-in or automatic emails need IT (docs/DEPLOYMENT.md step 4) | Not needed to go live |
