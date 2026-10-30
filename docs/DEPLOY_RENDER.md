# Deploying to Render (free) with MongoDB Atlas (free)

The quickest way to put the portal online. Render builds the repository's `Dockerfile` and runs it as one web
service (the React app and the API together); `render.yaml` holds all its settings. The database is a free
MongoDB Atlas M0 cluster.

```
git push ─▶ GitHub Actions (CI) passes ─▶ Render builds the Docker image ─▶ https://attendance-portal.onrender.com
                                                                                │
                                                                     MongoDB Atlas M0 (Mumbai)
```

**Free plan limits:** the service sleeps after 15 minutes without visits; the next visit takes about a minute to
wake it. 512 MB of memory. Upgrade the service to **Starter** in the Render dashboard to keep it always on.

## 1. MongoDB Atlas (5 minutes)

1. Sign up at https://www.mongodb.com/cloud/atlas/register and create an **M0 (Free)** cluster: AWS, **Mumbai**.
2. Create a database user (for example `portal`) with **Autogenerate Secure Password**. Copy the password.
3. **Network Access → Add IP Address → Allow access from anywhere (0.0.0.0/0).** Render's free services have no
   fixed outgoing address, so this is needed; the database user and password still protect the data.
4. **Database → Connect → Drivers**: copy the connection string and put the password in it:
   `mongodb+srv://portal:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0`

## 2. Render (5 minutes)

1. Sign up at https://render.com with **GitHub**, and allow Render to see the `attendance-project` repository.
2. **New → Blueprint** → choose `karthi251104/attendance-project` → Render reads `render.yaml`.
3. It asks for the values that are not stored in Git:

   | Name | Value |
   |---|---|
   | `MONGODB_URI` | the Atlas connection string from step 1 |
   | `HR_ADMIN_EMAILS` | the HR emails, comma-separated |
   | `INITIAL_HR_PASSWORD` | a first HR password (8+ characters); HR changes it after signing in |
   | `SENDER_MAILBOX`, `SMTP_USERNAME`, `SMTP_PASSWORD` | leave empty to send no emails, or see "Emails" below |

4. **Apply.** The first build takes about 5 minutes. The address is shown at the top, for example
   `https://attendance-portal.onrender.com` (the portal uses it in emails by itself; `APP_BASE_URL` is only
   needed for a custom domain).
5. Open `https://<your-address>/healthz`: it should say `{"status":"ok","database":true}`.

After that, every push to `main` deploys by itself once GitHub Actions has passed.

## 3. First use

1. Sign in as HR with `INITIAL_HR_PASSWORD`, then **Password** → set your own.
2. **Upload** the month's SAP file.
3. **Logins → Create logins**, and give each manager the link and their password.

## Emails (optional)

Render's **free plan blocks outgoing SMTP** (Gmail SMTP fails with `OSError`), so use **Brevo**, which sends
over HTTPS and is free for 300 emails a day:

1. Sign up at https://www.brevo.com.
2. **Senders, domains & dedicated IPs → Senders → Add a sender**: the HR email address. Open the confirmation
   email Brevo sends to it and click the link.
3. **SMTP & API → API keys → Generate a new API key**. Copy it (it is shown once).
4. Render → the service → **Environment**: `EMAIL_MODE=brevo`, `SENDER_MAILBOX=<the verified sender>`,
   `BREVO_API_KEY=<the key>`, optionally `SENDER_NAME=HR Attendance`. Save; Render restarts the service.

On a paid Render plan, Gmail SMTP also works: `EMAIL_MODE=smtp`, `SENDER_MAILBOX`, `SMTP_USERNAME`,
`SMTP_PASSWORD` (a Gmail App Password).

## Good to know

- **Logs:** Render → the service → **Logs**. They contain IDs and counts, never names or emails.
- **Roll back:** Render → the service → **Events** → an earlier deploy → **Rollback**.
- **Custom domain:** Render → the service → **Settings → Custom Domains**, then set `APP_BASE_URL` to it.
- **Atlas M0** keeps up to 512 MB (about 4 years of monthly uploads for ~100 employees) but has no automatic
  backups; download the workbook each month, or move to a paid tier for backups.
- Moving to Azure later needs no code change: see [DEPLOYMENT.md](DEPLOYMENT.md).
