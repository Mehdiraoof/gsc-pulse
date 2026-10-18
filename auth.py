"""
PulseO: who PulseO acts as
==========================
A site is read either as the robot (the service account, today's way: the client adds the robot
to Search Console and shares the sheets with it) or as the owner (`"auth": "me"`): PulseO then uses
the owner's own Google account through an OAuth refresh token kept in the GSC_OAUTH_JSON secret

    {"client_id": "...", "client_secret": "...", "refresh_token": "..."}

with exactly two permissions:
  * webmasters.readonly  read Search Console, never change it
  * drive.file           create files, and use only the files PulseO itself created

One Search Console client and one Sheets / Drive client are built per auth type and shared by all
sites of that type. Nothing in this module prints or returns a secret; the error messages say what
is wrong, never what the value is.
"""

import json
from dataclasses import dataclass

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

TOKEN_URI = "https://oauth2.googleapis.com/token"
ME_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
SECRET_KEYS = ("client_id", "client_secret", "refresh_token")
ENV_NAME = "GSC_OAUTH_JSON"


class AuthError(Exception):
    """Credentials are unusable. The message is safe to print."""


class AccessLost(AuthError):
    """The refresh token was revoked or expired (invalid_grant): the owner has to authorize again."""


@dataclass
class Clients:
    """The Google clients of one auth type."""
    gsc: object
    sheets: object
    drive: object = None


def secret_values(env) -> list:
    """The secret parts of GSC_OAUTH_JSON, for the scrubber (empty when not set or unreadable)."""
    try:
        data = json.loads(env.get(ENV_NAME) or "")
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return [str(data[k]) for k in SECRET_KEYS if data.get(k)]


def me_credentials(env) -> Credentials:
    """Credentials of the owner's Google account, from GSC_OAUTH_JSON. No network call."""
    raw = env.get(ENV_NAME)
    if not raw or not raw.strip():
        raise AuthError(f"{ENV_NAME} is not set (sites with \"auth\": \"me\" need it)")
    try:
        data = json.loads(raw)
    except ValueError:
        raise AuthError(f"{ENV_NAME} is not valid JSON") from None
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k].strip()
                                         for k in SECRET_KEYS):
        raise AuthError(f"{ENV_NAME} must be a JSON object with client_id, client_secret and "
                        f"refresh_token")
    return Credentials(token=None, refresh_token=data["refresh_token"].strip(), token_uri=TOKEN_URI,
                       client_id=data["client_id"].strip(), client_secret=data["client_secret"].strip(),
                       scopes=ME_SCOPES)


def check_access(creds, request=Request) -> None:
    """Gets a first access token. A revoked or expired refresh token raises AccessLost."""
    try:
        creds.refresh(request())
    except RefreshError as e:
        if "invalid_grant" in str(e):
            raise AccessLost("the refresh token was revoked or has expired") from None
        raise AuthError("Google refused the OAuth credentials") from None
    except Exception as e:  # noqa: BLE001  network problems and the like
        raise AuthError(f"could not reach Google to refresh the OAuth credentials "
                        f"({type(e).__name__})") from None


def build_me_clients(env, build, request=Request) -> Clients:
    """Search Console, Sheets and Drive clients acting as the owner. `build` is
    googleapiclient.discovery.build (a parameter so tests can replace it)."""
    creds = me_credentials(env)
    check_access(creds, request)
    return Clients(gsc=build("webmasters", "v3", credentials=creds),
                   sheets=build("sheets", "v4", credentials=creds),
                   drive=build("drive", "v3", credentials=creds))


def access_to_property(gsc, site_url: str) -> bool:
    """Is this exact property in the account's Search Console list, with a permission above
    'unverified user'? The list itself is never logged."""
    entries = gsc.sites().list().execute(num_retries=3).get("siteEntry", [])
    return any(e.get("siteUrl") == site_url and e.get("permissionLevel") != "siteUnverifiedUser"
               for e in entries)
