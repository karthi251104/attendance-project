# Deploying to Azure

After a one-time setup, **every push to `main` deploys automatically**: GitHub Actions runs all tests, builds
the Docker image, pushes it to Azure Container Registry, switches the Web App to it and waits until
`/healthz` answers.

```
git push ─▶ GitHub Actions: CI (tests + build + Docker smoke test) ─▶ build image ─▶ Azure Container Registry
                                                                                        │
                                          https://<app>.azurewebsites.net ◀── App Service pulls the image
```

## What you need

- An Azure subscription where you can create resources (Contributor).
- Admin access to the GitHub repository.
- Only if you want Microsoft sign-in or automatic emails (step 4, optional): a Microsoft 365 admin.

## 1. Create the Azure resources (once, about 10 minutes)

Open **Azure Cloud Shell** (Bash) in the Azure portal, then:

```bash
git clone https://github.com/karthi251104/attendance-project && cd attendance-project
bash infra/azure-setup.sh
```

It creates a resource group, a container registry, a Linux App Service plan (B1) with a container Web App,
Cosmos DB for MongoDB (continuous backup) and a GitHub deploy identity that signs in with OpenID Connect (no
password stored anywhere). It sets `ENV=production`, `SECRET_KEY`, `MONGODB_URI`, `APP_BASE_URL` and the health
check. At the end it prints the values for step 2. Change the region or names with variables, for example
`LOCATION=southindia APP=attendance-hr bash infra/azure-setup.sh`.

## 2. Connect GitHub

In the repository: **Settings → Secrets and variables → Actions**.

| Kind | Name | Value (printed by the script) |
|---|---|---|
| Secret | `AZURE_CLIENT_ID` | the deploy identity's application ID |
| Secret | `AZURE_TENANT_ID` | your tenant ID |
| Secret | `AZURE_SUBSCRIPTION_ID` | your subscription ID |
| Variable | `AZURE_WEBAPP_NAME` | the Web App name |
| Variable | `ACR_NAME` | the registry name |
| Variable | `APP_URL` | `https://<app>.azurewebsites.net` |

Then **Settings → Environments → New environment** `production`. Optional: add yourself as a required
reviewer, so each deploy waits for your approval.

## 3. Finish the app settings

**Web App → Settings → Environment variables**, add:

| Name | Value |
|---|---|
| `HR_ADMIN_EMAILS` | the HR people, comma-separated |
| `INITIAL_HR_PASSWORD` | a first password for HR (at least 8 characters). Each HR email gets a login with it when the app starts; HR then changes it under **Password** |

Sign-in is by **ID and password** (`AUTH_MODE=password`, set by the script): the ID is the person's work email.
After the first upload, HR opens **Logins → Create logins**, copies or downloads the generated passwords and gives
each manager their own. Managers can change their password after signing in; HR can reset a forgotten one or
disable a login. Five wrong passwords lock a login for 15 minutes.

Emails stay off (`EMAIL_MODE=console`): HR sends managers the portal link and their password themselves.
To let **Email all managers** really send without IT, use a mailbox's SMTP: `EMAIL_MODE=smtp`, `SENDER_MAILBOX`,
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` (for Gmail: `smtp.gmail.com`, `587` and an App Password).
The email has the portal link and says to sign in with the email address and the password HR gave; it never
contains a password.

## 4. Optional: Microsoft sign-in and automatic emails

Only if IT agrees. One Entra ID app registration does both.

1. **Entra admin centre → App registrations → New registration**: name `Attendance Portal`, accounts in this
   organisational directory only, redirect URI platform **Web** `https://<app>.azurewebsites.net/auth/callback`.
2. **Certificates & secrets → New client secret.** Note the expiry date and renew it before then.
3. **API permissions → Add → Microsoft Graph**: delegated `openid`, `profile`, `email`, `User.Read` (sign-in);
   application `Mail.Send` (emails). Click **Grant admin consent**.
4. **Restrict `Mail.Send` to the sender mailbox** (otherwise the app could send as anyone). An Exchange admin runs:

   ```powershell
   New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "Attendance Portal"
   New-ManagementScope -Name "Attendance sender" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'hr.attendance@company.com'"
   New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send" -CustomResourceScope "Attendance sender"
   ```

   Then remove the tenant-wide `Mail.Send` consent, so only the scoped assignment grants it.
5. Add the settings:

   | Name | Value |
   |---|---|
   | `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | from above (better: Key Vault references) |
   | `AUTH_MODE` | `entra`, for Microsoft sign-in instead of passwords |
   | `EMAIL_MODE`, `SENDER_MAILBOX` | `graph` and `hr.attendance@company.com`, for automatic emails |

   Emails can be switched on while keeping passwords, and the other way round.
## 5. Deploy

Push to `main`, or **Actions → Deploy → Run workflow**. The run shows the URL when it is healthy.

## Day to day

- **Logs:** Web App → Log stream. Logs contain IDs and counts, never names or emails.
- **Roll back:** Web App → Deployment Center → choose an earlier image tag (each one is the Git commit SHA), or
  revert the commit and push.
- **Scale:** change the App Service plan (B1 is enough for a few hundred managers), or set `WEB_CONCURRENCY`.
- **Backups:** Cosmos DB continuous backup gives point-in-time restore for 30 days.
- **Dependency updates:** Dependabot opens weekly pull requests; CI checks each one.
