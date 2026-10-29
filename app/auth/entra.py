"""Microsoft Entra ID (Azure AD) login: OpenID Connect authorization code flow with MSAL.

Delegated permissions needed on the app registration: openid, profile, email, User.Read.
"""

from typing import Any

import msal

from app.config import Settings

SCOPES = ["User.Read"]  # MSAL adds openid / profile / offline_access itself


class LoginFailed(Exception):
    pass


def _client(settings: Settings) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        client_credential=settings.entra_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
    )


def redirect_uri(settings: Settings) -> str:
    return f"{settings.app_base_url.rstrip('/')}/auth/callback"


def start_login(settings: Settings) -> dict[str, Any]:
    """Return the MSAL flow dict; store it in the session and redirect the user to flow['auth_uri']."""
    return _client(settings).initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri(settings), prompt="select_account")


def finish_login(settings: Settings, flow: dict[str, Any], query_params: dict[str, str]) -> tuple[str, str]:
    """Exchange the code for tokens and return (email, display name) from the ID token."""
    try:
        result = _client(settings).acquire_token_by_auth_code_flow(flow, query_params)
    except ValueError as exc:  # state mismatch or replay
        raise LoginFailed("The sign-in response was not valid. Please try again.") from exc
    if "error" in result:
        raise LoginFailed(result.get("error_description") or result["error"])
    claims = result.get("id_token_claims") or {}
    if claims.get("tid") and claims["tid"] != settings.entra_tenant_id:
        raise LoginFailed("This account belongs to a different organisation.")
    email = claims.get("email") or claims.get("preferred_username") or ""
    if "@" not in email:
        raise LoginFailed("Your account has no email address.")
    return email.lower(), claims.get("name") or email
