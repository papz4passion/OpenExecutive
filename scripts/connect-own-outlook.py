#!/usr/bin/env python3
"""Connect YOUR OWN Outlook for Act as me, or the Executive's own mailbox
(one-time, per mailbox).

Act as me lets the Executive write drafts as you, in your own Outlook —
never from its own account, and never through the general tool-calling
surface. This mints the credential it uses: a browser sign-in as you, asking
only for Outlook read + draft (Mail.ReadWrite) access, and writes one file
named from a hash of your address. The Executive's own Outlook mailbox
(inbound polling + native send/reply/draft tools) uses the same flow, signed
in as EXEC_EMAIL_ADDRESS instead — see docs/outlook_setup.md.

Run it where you can open a browser, with the Azure AD app registration
exported (see docs/outlook_setup.md for creating one):

    export MICROSOFT_OAUTH_CLIENT_ID=...
    export MICROSOFT_OAUTH_CLIENT_SECRET=...
    export MICROSOFT_OAUTH_TENANT_ID=common
    uv run python scripts/connect-own-outlook.py --email you@example.com

The file lands in $DELEGATION_OUTLOOK_CREDENTIALS_DIR (default:
./delegation-outlook-credentials). Copy it onto the API's volume — for Act as
me, into the directory DELEGATION_OUTLOOK_CREDENTIALS_DIR points at (Docker:
/data/delegation_outlook/); for the Executive's own mailbox, anywhere, and
point EXEC_OUTLOOK_CREDENTIALS_PATH at that exact file. No restart is needed
for Act as me — it is read on each use. Sign in as the address on your
People entry (or EXEC_EMAIL_ADDRESS): any other account is refused when it
is used. Do NOT commit the file — it holds your refresh token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# Must match integrations.outlook_client.DELEGATED_SCOPES exactly — asserted
# by a unit test, the same guard connect-own-gmail.py's script has.
SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "offline_access",
]
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
CREDENTIAL_VERSION = 1
_CALLBACK_TIMEOUT_S = 300


def email_key(email: str) -> str:
    """The file stem for ``email`` — must match delegation.outlook / .../
    integrations.outlook_client's ``email_key``."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def credential_payload(
    email: str, refresh_token: str, client_id: str, client_secret: str, tenant_id: str
) -> dict[str, Any]:
    """The file's content — the format outlook_client.load_credential reads."""
    return {
        "version": CREDENTIAL_VERSION,
        "email": email.strip().lower(),
        "scopes": SCOPES,
        "authorized_user": {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "tenant_id": tenant_id,
        },
    }


def write_credential(directory: pathlib.Path, email: str, payload: dict[str, Any]) -> pathlib.Path:
    """Write the file with owner-only permissions (0700 directory, 0600 file)."""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{email_key(email)}.json"
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def _need(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        sys.exit(f"error: {key} must be exported in this shell first")
    return value


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.result["code"] = params["code"][0]
            body = b"Signed in. You can close this tab and return to the terminal."
        else:
            error = params.get("error_description", params.get("error", ["unknown error"]))[0]
            _CallbackHandler.result["error"] = error
            body = b"Sign-in failed. You can close this tab and return to the terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, log_format: str, *args: Any) -> None:
        pass  # silence the default per-request stderr log


def _authorize(tenant_id: str, client_id: str, email: str) -> tuple[str, str]:
    """Run the local-redirect auth-code flow; return (code, redirect_uri)."""
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"
    auth_url = "https://login.microsoftonline.com/{}/oauth2/v2.0/authorize?{}".format(
        tenant_id,
        urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(SCOPES),
            "login_hint": email,
            "prompt": "select_account",
        }),
    )
    print(f"Opening a browser: sign in as {email} and allow read + draft access to Outlook.")
    webbrowser.open(auth_url)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=_CALLBACK_TIMEOUT_S)
    server.server_close()
    if "error" in _CallbackHandler.result:
        sys.exit(f"error: sign-in failed: {_CallbackHandler.result['error']}")
    code = _CallbackHandler.result.get("code")
    if not code:
        sys.exit(f"error: timed out waiting for sign-in ({_CALLBACK_TIMEOUT_S}s). Run it again.")
    return code, redirect_uri


def _exchange_code(
    tenant_id: str, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "scope": " ".join(SCOPES),
    }).encode()
    request = urllib.request.Request(token_url, data=data, method="POST")  # noqa: S310 — fixed https URL
    with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 — fixed https URL
        return dict(json.load(resp))


def _profile_email(access_token: str) -> str:
    request = urllib.request.Request(GRAPH_ME_URL, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310 — fixed https URL
        payload = json.load(resp)
    return str(payload.get("mail") or payload.get("userPrincipalName") or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True, help="The Outlook address to connect (yours, or the Executive's)")
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email:
        sys.exit("error: --email must be an email address")

    client_id = _need("MICROSOFT_OAUTH_CLIENT_ID")
    client_secret = _need("MICROSOFT_OAUTH_CLIENT_SECRET")
    tenant_id = _need("MICROSOFT_OAUTH_TENANT_ID")

    code, redirect_uri = _authorize(tenant_id, client_id, email)
    try:
        tokens = _exchange_code(tenant_id, client_id, client_secret, code, redirect_uri)
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: token exchange failed: {exc.read().decode(errors='replace')}")
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not refresh_token or not access_token:
        sys.exit("error: Microsoft returned no refresh token — ensure offline_access is granted and try again.")
    opened = _profile_email(str(access_token))
    if opened != email:
        sys.exit(f"error: you signed in as {opened}, not {email}. Run it again as {email}.")

    directory = pathlib.Path(
        os.environ.get("DELEGATION_OUTLOOK_CREDENTIALS_DIR") or pathlib.Path.cwd() / "delegation-outlook-credentials"
    )
    path = write_credential(
        directory, email, credential_payload(email, str(refresh_token), client_id, client_secret, tenant_id)
    )
    print(f"Saved {path}")
    print(
        "Act as me: copy it into the API's DELEGATION_OUTLOOK_CREDENTIALS_DIR "
        "(Docker: /data/delegation_outlook/), then open Settings → Act as me."
    )
    print(
        "Executive's own mailbox: copy it anywhere and set "
        "EXEC_OUTLOOK_CREDENTIALS_PATH to that exact file."
    )
    print("Do not commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
