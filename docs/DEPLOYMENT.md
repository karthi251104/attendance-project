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

- An Azure subscription where you can create resources (Contributor), and permission to create an app
  registration in Entra ID.
- A Microsoft 365 admin who can grant admin consent and restrict `Mail.Send` (step 3).
- Admin access to the GitHub repository.

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

## 3. Microsoft sign-in and email (Entra ID app registration)

One registration does both.

1. **Entra admin centre → App registrations → New registration**: name `Attendance Portal`, accounts in this
   organisational directory only, redirect URI platform **Web** `https://<app>.azurewebsites.net/auth/callback`.
2. **Certificates & secrets → New client secret.** Note the expiry date and renew it before then.
3. **API permissions → Add → Microsoft Graph**: delegated `openid`, `profile`, `email`, `User.Read`;
   application `Mail.Send`. Click **Grant admin consent**.
4. **Restrict `Mail.Send` to the sender mailbox** (otherwise the app could send as anyone). An Exchange admin runs:

   ```powershell
   New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "Attendance Portal"
   New-ManagementScope -Name "Attendance sender" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'hr.attendance@company.com'"
   New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send" -CustomResourceScope "Attendance sender"
   ```

   Then remove the tenant-wide `Mail.Send` consent, so only the scoped assignment grants it.

## 4. Finish the app settings

**Web App → Settings → Environment variables**, add:

| Name | Value |
|---|---|
| `HR_ADMIN_EMAILS` | the HR people, comma-separated |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | from step 3 (better: Key Vault references) |
| `EMAIL_MODE` | `graph` |
| `SENDER_MAILBOX` | `hr.attendance@company.com` |
| `EMAIL_CC` | optional |

The app refuses to start in production while the Entra settings are missing, so do this before the first deploy.

## 5. Deploy

Push to `main`, or **Actions → Deploy → Run workflow**. The run shows the URL when it is healthy.

## Day to day

- **Logs:** Web App → Log stream. Logs contain IDs and counts, never names or emails.
- **Roll back:** Web App → Deployment Center → choose an earlier image tag (each one is the Git commit SHA), or
  revert the commit and push.
- **Scale:** change the App Service plan (B1 is enough for a few hundred managers), or set `WEB_CONCURRENCY`.
- **Backups:** Cosmos DB continuous backup gives point-in-time restore for 30 days.
- **Dependency updates:** Dependabot opens weekly pull requests; CI checks each one.
