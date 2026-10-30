# Testing before go-live

Three levels, from fastest to most real:

| Level | What it proves | Needs |
|---|---|---|
| Automated tests | Rules, Excel reading/writing, permissions, API, UI logic | Nothing (runs in CI on every push) |
| Local demo (`DEV_LOGIN=true`) | The whole flow on your PC, any email, no password, emails printed | Python + Node |
| **Dev tunnel** | Real Microsoft sign-in, real emails, real managers on their own laptops and phones | The items in "What is needed" |

## Dev tunnel: test with real sign-in and real email, before Azure

A dev tunnel gives the portal on your PC a public `https://…devtunnels.ms` address, so managers can open the
email link from anywhere while it still runs on your machine.

### One-time setup

1. Install the tunnel tool and sign in with your work account:

   ```powershell
   winget install Microsoft.devtunnel
   devtunnel user login
   ```

2. Create a tunnel that keeps the same address every time (the address must not change, because it is
   registered in Entra ID):

   ```powershell
   devtunnel create attendance-test --allow-anonymous
   devtunnel port create attendance-test -p 8000
   devtunnel show attendance-test
   ```

   Note the URL for port 8000, for example `https://abcd1234-8000.inc1.devtunnels.ms`.
   `--allow-anonymous` lets managers reach the page without a dev-tunnel account; the portal itself still
   requires Microsoft sign-in.

3. In the Entra app registration (docs/DEPLOYMENT.md step 3), add the redirect URI
   `https://abcd1234-8000.inc1.devtunnels.ms/auth/callback`.

4. Create `.env` in the repository root:

   ```ini
   ENV=development
   APP_BASE_URL=https://abcd1234-8000.inc1.devtunnels.ms
   SECRET_KEY=<any long random text>
   MONGODB_URI=mongodb://localhost:27017      # docker compose up -d mongo; or mongomock:// (lost on restart)
   DEV_LOGIN=false
   HR_ADMIN_EMAILS=you@company.com
   ENTRA_TENANT_ID=...
   ENTRA_CLIENT_ID=...
   ENTRA_CLIENT_SECRET=...
   EMAIL_MODE=graph                           # or console to only print emails while you start
   SENDER_MAILBOX=hr.attendance@company.com
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
Stop with Ctrl+C in both terminals. Tip: for a test with real managers, use a copy of the workbook where you
changed a few `Email ID` cells of managers in HC_Teamlease to the testers' work emails, so nobody else gets mail.

## Test checklist

Tick each one. ✱ = needs real sign-in or email (dev tunnel or Azure).

### Upload (HR)
- [ ] Upload the SAP file with SAP + HC_Teamlease: the review page shows the right employee and manager counts.
- [ ] Upload a file that still has TO CONSIDER: a clear error, nothing saved.
- [ ] Upload a file without HC_Teamlease, a `.csv`, a file over 25 MB: clear errors.
- [ ] Exceptions list the employees missing from HC_Teamlease and unknown schedules; the CSV downloads.
- [ ] Upload the same month again: the new upload becomes active, the old one shows "Older version".

### Sign-in ✱
- [ ] HR email → HR screens. A manager's email → only "Your team's attendance".
- [ ] Someone who is neither → "Nothing to review".
- [ ] An account from another organisation is refused.
- [ ] Sign out, then press Back: the data is not shown again.
- [ ] After 8 hours the session ends and asks to sign in again.

### Email ✱
- [ ] "Email all managers": each tester gets exactly one email from the HR mailbox, with the team size and link.
- [ ] The email contains no employee names or IDs.
- [ ] Resend to one manager works; a manager without an email shows **Failed** with the reason.
- [ ] The link in the email opens the team page after sign-in.

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
| 1 | **Entra ID app registration**: tenant ID, client ID, client secret (or permission for me to create it) | Microsoft sign-in and sending email |
| 2 | **Sender mailbox**, e.g. `hr.attendance@company.com`, and an Exchange admin to scope `Mail.Send` to it | Manager emails |
| 3 | **Admin consent** for the app's Graph permissions | Sign-in and Mail.Send |
| 4 | **HR admin emails** | Who sees the HR screens |
| 5 | **2–3 test managers** (work accounts) and which employees they manage | Real manager testing |
| 6 | **A MongoDB** for testing: Docker on the test PC, or the Cosmos DB from the Azure setup | Data that survives restarts |
| 7 | **Azure subscription** access (Contributor) to run `infra/azure-setup.sh` | Production |
| 8 | **GitHub repo admin** to add the secrets and the `production` environment | Automatic deploys |
| 9 | Optional: a **custom domain** (e.g. `attendance.company.com`) and its certificate | A friendlier address |
| 10 | Confirm the **PL** column name: the sheet calls it "Maternity Leave"; is it paternity or privilege leave? | Labels in the sheet and the portal |
