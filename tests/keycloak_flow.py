"""Log in to the local Keycloak as a browser would, for integration tests.

The flow is the one Open WebUI uses: authorization code with PKCE. The
helper asks for the login page, posts the user's name and password to the
form Keycloak returns, takes the code from the redirect without following
it, and exchanges the code for tokens. Each step that Keycloak refuses
raises `LoginRefused` with the step, the status, and Keycloak's own reason,
never a secret.
"""

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ISSUER = "http://localhost:8180/realms/knowledge-service"
CLIENT_ID = "open-webui"
REDIRECT_URI = "http://localhost:3000/oauth/oidc/callback"
SECRETS = Path(__file__).resolve().parents[1] / "deploy/keycloak/.env"
FORM_ACTION = re.compile(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"')
# The message on Keycloak's error page, and the one above its login form.
PAGE_ERROR = re.compile(r'id="kc-error-message">\s*<p class="instruction">([^<]*)<')
FORM_ERROR = re.compile(r'kc-feedback-text">\s*([^<]*?)\s*<')


class LoginRefused(Exception):
    """Keycloak refused one step of the login.

    `step` names the step, `status` is Keycloak's HTTP status, and `reason`
    is the message Keycloak gave: the text of its error page or login form,
    or the `error` and `error_description` of a redirect.
    """

    def __init__(self, step: str, status: int, reason: str) -> None:
        super().__init__(f"{step} refused with status {status}: {reason}")
        self.step = step
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Tokens:
    access_token: str
    id_token: str


def local_secrets(path: Path = SECRETS) -> dict[str, str]:
    """The generated secrets, read from the ignored `.env` file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines if "=" in line]
    return {name: value for name, value in pairs if not name.startswith("#")}


def claims(token: str) -> dict[str, Any]:
    """The payload of a JWT, decoded but not verified; M07-T03 verifies."""
    return _segment(token.split(".")[1])


def header(token: str) -> dict[str, Any]:
    """The header of a JWT, decoded but not verified."""
    return _segment(token.split(".")[0])


async def log_in(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    *,
    client_secret: str,
    redirect_uri: str = REDIRECT_URI,
    scope: str = "openid",
    challenge: str | None = "S256",
) -> Tokens:
    """Log `username` in through the login form and return their tokens.

    `challenge` is the PKCE method to ask for; `None` sends no challenge.
    """
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if challenge == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        query["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    elif challenge is not None:
        query["code_challenge"] = verifier
    if challenge is not None:
        query["code_challenge_method"] = challenge
    page = await client.get(f"{ISSUER}/protocol/openid-connect/auth", params=query)
    action = FORM_ACTION.search(page.text) if page.status_code == 200 else None
    if action is None:
        raise LoginRefused("authorization request", page.status_code, _reason(page))

    answer = await client.post(
        html.unescape(action[1]),
        data={"username": username, "password": password, "credentialId": ""},
        headers={"Cookie": _session_cookies(page)},
    )
    location: str = answer.headers.get("location", "")
    returned: dict[str, list[str]] = parse_qs(urlsplit(location).query)
    if answer.status_code != 302 or not location.startswith(redirect_uri):
        raise LoginRefused("login form", answer.status_code, _reason(answer))
    if returned.get("state") != [state] or "code" not in returned:
        raise LoginRefused("redirect", answer.status_code, _reason(answer))

    exchanged = await client.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": returned["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        auth=(CLIENT_ID, client_secret),
    )
    if exchanged.status_code != 200:
        raise LoginRefused("code exchange", exchanged.status_code, _reason(exchanged))
    body = exchanged.json()
    return Tokens(access_token=body["access_token"], id_token=body["id_token"])


def _reason(response: httpx.Response) -> str:
    """What Keycloak said, from a redirect, a JSON error, or a page."""
    location: str = response.headers.get("location", "")
    if location:
        parts = urlsplit(location)
        said: dict[str, list[str]] = parse_qs(parts.query or parts.fragment)
        return " ".join(said.get("error", []) + said.get("error_description", []))
    if response.headers.get("content-type", "").startswith("application/json"):
        body: dict[str, str] = response.json()
        return f"{body.get('error', '')} {body.get('error_description', '')}".strip()
    found = PAGE_ERROR.search(response.text) or FORM_ERROR.search(response.text)
    return html.unescape(found[1]).strip() if found else ""


def _session_cookies(response: httpx.Response) -> str:
    """The login session's cookies, as a `Cookie` header.

    Keycloak marks them `Secure`. A browser still sends them to
    `http://localhost`, which it treats as secure; httpx does not, so the
    helper passes them on itself.
    """
    cookies = response.headers.get_list("set-cookie")
    return "; ".join(cookie.split(";", 1)[0] for cookie in cookies)


def _segment(part: str) -> dict[str, Any]:
    decoded = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    return json.loads(decoded)  # type: ignore[no-any-return]
