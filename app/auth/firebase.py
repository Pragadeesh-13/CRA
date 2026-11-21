"""Firebase Identity Toolkit operations used by the operator console."""

import base64
import json
import time

import httpx

from app.config import settings


class IdentityError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


async def identity_request(operation: str, payload: dict) -> dict:
    if not settings.firebase_project_id or not settings.firebase_web_api_key:
        raise IdentityError(503, "Account authentication is not configured.")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:{operation}",
                params={"key": settings.firebase_web_api_key},
                json=payload,
            )
        if response.status_code in {400, 401}:
            raise IdentityError(401, "The identity provider rejected the credentials.")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid identity response")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise IdentityError(503, "The identity provider is unavailable.") from exc


def _claims_for_project(token: str) -> dict:
    # Claims constrain the project here. Identity Toolkit verifies the token signature.
    project = settings.firebase_project_id
    if not project or not settings.firebase_web_api_key:
        raise IdentityError(503, "Account authentication is not configured.")
    try:
        _, payload, _ = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(claims, dict):
            raise ValueError("Invalid claims")
        if (
            claims.get("aud") != project or claims.get("iss") != f"https://securetoken.google.com/{project}"
            or not isinstance(claims.get("exp"), int) or claims["exp"] <= time.time()
            or not isinstance(claims.get("sub"), str) or not claims["sub"]
        ):
            raise ValueError("Invalid project, expiry or subject")
    except ValueError as exc:
        raise IdentityError(401, "A valid identity token for this Firebase project is required.") from exc
    return claims


async def verified_user(token: str, *, google: bool = False) -> dict:
    claims = _claims_for_project(token)
    firebase = claims.get("firebase")
    if google and (not isinstance(firebase, dict) or firebase.get("sign_in_provider") != "google.com"):
        raise IdentityError(401, "A Firebase Google sign-in token is required.")
    data = await identity_request("lookup", {"idToken": token})
    users = data.get("users") or []
    if len(users) != 1 or not isinstance(users[0], dict):
        raise IdentityError(401, "The identity provider did not verify the account.")
    account = users[0]
    if (
        account.get("localId") != claims["sub"] or not account.get("email")
        or account.get("emailVerified") is not True or account.get("disabled")
    ):
        raise IdentityError(401, "An active account with a verified email address is required.")
    return {
        "id": f"firebase:{settings.firebase_project_id}:{account['localId']}",
        "email": account["email"],
        "fullName": account.get("displayName") or account["email"],
        "photoURL": account.get("photoUrl") or "",
    }
