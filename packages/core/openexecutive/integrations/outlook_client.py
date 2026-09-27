"""A direct Microsoft Graph client for Outlook mail, shared by both tracks.

There is no "workspace-mcp" equivalent for Outlook in this project, so both
the Executive's own mailbox (poller + native send/reply/draft tools) and Act
as me (``delegation/outlook.py``) talk to Graph directly over ``httpx``,
mirroring ``delegation/gmail.py``'s direct-REST approach.

**Shared vs. own-mailbox-only.** ``_GraphMailBase`` holds everything both
tracks need: OAuth token refresh, the HTTP wrapper, message parsing, and
read/draft operations. ``OutlookClient`` adds ``send``/``list_unread``/
``mark_read`` — capabilities only the Executive's own mailbox needs.
``delegation/outlook.py``'s ``DelegateOutlook`` subclasses ``_GraphMailBase``
directly (never ``OutlookClient``), so a delegated mailbox has no send
method anywhere in its MRO — the same "no send method exists" invariant
Gmail's ``DelegateGmail`` holds, verified by a unit test that introspects
``delegation/outlook.py``'s source specifically (this module legitimately
contains ``send()`` for the Executive's own mailbox).

**Credential.** One JSON file, ``{"version": 1, "email": ..., "authorized_user":
{refresh_token, client_id, client_secret, tenant_id}}``. Act as me stores one
file per person (hashed filename, like Gmail); the Executive's own mailbox
uses a single fixed path (``EXEC_OUTLOOK_CREDENTIALS_PATH`` — Graph has no
MCP server to pick a credential from a shared directory the way workspace-mcp
does, so there is nothing to separate this credential *from* the way
``delegation_google_credentials_dir`` is kept out of
``WORKSPACE_MCP_CREDENTIALS_DIR``).

**Scopes.** ``Mail.ReadWrite`` for both tracks (Graph has no compose-only
scope the way Gmail's ``gmail.compose`` is); ``Mail.Send`` additionally for
the Executive's own mailbox only. ``offline_access`` for a refresh token.

**Threading.** Graph's ``createReply``/``createReplyAll`` endpoints build a
correctly threaded draft (quoted body, ``In-Reply-To``/``References``, the
same ``conversationId``) from an existing message, unlike Gmail's raw-MIME
approach where this package sets those headers itself. ``create_draft`` uses
``createReply`` when replying to a message and a plain create otherwise.
Graph also only accepts custom ``x-`` prefixed headers via
``internetMessageHeaders`` on message create/update — standard headers like
``In-Reply-To`` cannot be forced that way on a *new* (non-reply) draft, so
threading a brand-new message you didn't reply to isn't attempted here.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from openexecutive.delegation.gmail import clean_header, email_key, html_to_text, normalize_email

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0/me"
_GRAPH_SCOPE_PREFIX = "https://graph.microsoft.com/"
SCOPE_MAIL_READWRITE = f"{_GRAPH_SCOPE_PREFIX}Mail.ReadWrite"
SCOPE_MAIL_SEND = f"{_GRAPH_SCOPE_PREFIX}Mail.Send"
SCOPE_OFFLINE_ACCESS = "offline_access"
# Delegated (Act as me): read + draft only.
DELEGATED_SCOPES: tuple[str, ...] = (SCOPE_MAIL_READWRITE, SCOPE_OFFLINE_ACCESS)
# The Executive's own mailbox: adds Mail.Send.
OWN_MAILBOX_SCOPES: tuple[str, ...] = (SCOPE_MAIL_READWRITE, SCOPE_MAIL_SEND, SCOPE_OFFLINE_ACCESS)
CREDENTIAL_VERSION = 1

# Marks a draft this package wrote. Best-effort only, and only settable on a
# message created via a reply/plain create (Graph rejects non-"x-" headers on
# update in most cases too).
GHOSTWRITTEN_HEADER = "X-OE-Ghostwritten"

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,100}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_=\-]{1,300}$")
_TOKEN_SLACK_SECONDS = 60
_FETCH_CONCURRENCY = 5


class GraphError(Exception):
    """A Graph call failed (network, 5xx, an unexpected response)."""


class GraphNotConfigured(GraphError):
    """No credential for this mailbox."""


class GraphAuthError(GraphError):
    """Microsoft refused the credential: revoked, expired, or missing a scope."""


def _token_uri(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


@dataclass(frozen=True)
class GraphCredential:
    email: str
    refresh_token: str
    client_id: str
    client_secret: str
    tenant_id: str


@dataclass
class MailMessage:
    id: str
    thread_id: str  # Graph's conversationId
    from_addr: str = ""
    from_name: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    reply_to: str = ""
    subject: str = ""
    date: str = ""
    message_id_header: str = ""
    references: str = ""
    text: str = ""
    # "DRAFT" when Graph's isDraft is true — enough for delegation_tools.py's
    # Gmail-derived thread filtering (`"DRAFT" not in m.labels`), which this
    # module's messages are also read by via mailbox_for(). Graph has no
    # Gmail-style SENT label; delegation_tools.py's own-address check
    # (`from_addr != own`) already covers that case.
    labels: list[str] = field(default_factory=list)
    mailing_list: bool = False
    auto_generated: bool = False
    ghostwritten: bool = False


@dataclass
class MailThread:
    id: str
    messages: list[MailMessage]


@dataclass
class ThreadSummary:
    id: str
    subject: str
    sender: str
    date: str


@dataclass
class DraftSpec:
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    thread_id: str | None = None  # the Graph message id to reply to, if any
    in_reply_to: str | None = None
    references: str | None = None
    from_name: str = ""


@dataclass
class CreatedDraft:
    draft_id: str
    message_id: str
    thread_id: str
    # Graph returns a ready-to-use Outlook-on-the-web URL on every message
    # create/update — no separate link-builder needed the way Gmail's
    # gmail_link() constructs one from a fixed URL prefix.
    web_link: str = ""


# --------------------------------------------------------------------------- #
# Credential file
# --------------------------------------------------------------------------- #


def credentials_dir() -> Path:
    from openexecutive.config import get_settings

    return Path(get_settings().delegation_outlook_credentials_dir)


def credential_path(email: str, *, directory: Path | None = None) -> Path:
    return (directory or credentials_dir()) / f"{email_key(email)}.json"


def _parse_credential(data: object, *, expect_email: str, source: str) -> GraphCredential | None:
    if not isinstance(data, dict) or normalize_email(str(data.get("email") or "")) != expect_email:
        logger.warning("integrations.outlook_client: credential %s is for another address", source)
        return None
    user = data.get("authorized_user")
    if not isinstance(user, dict):
        return None
    values = {k: user.get(k) for k in ("refresh_token", "client_id", "client_secret", "tenant_id")}
    if not all(isinstance(v, str) and v for v in values.values()):
        logger.warning("integrations.outlook_client: credential %s is incomplete", source)
        return None
    if not _TENANT_RE.match(str(values["tenant_id"])):
        logger.warning("integrations.outlook_client: credential %s names an unexpected tenant", source)
        return None
    return GraphCredential(
        email=expect_email,
        refresh_token=str(values["refresh_token"]),
        client_id=str(values["client_id"]),
        client_secret=str(values["client_secret"]),
        tenant_id=str(values["tenant_id"]),
    )


def load_credential(email: str, *, directory: Path | None = None) -> GraphCredential | None:
    """The delegated (Act as me) credential for ``email``, or None when there
    is none or it is unreadable (logged). A file naming another address is
    ignored."""
    address = normalize_email(email)
    if not address:
        return None
    path = credential_path(address, directory=directory)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("integrations.outlook_client: unreadable credential file %s", path.name)
        return None
    return _parse_credential(data, expect_email=address, source=path.name)


def load_exec_credential() -> GraphCredential | None:
    """The Executive's own Outlook mailbox credential
    (``EXEC_OUTLOOK_CREDENTIALS_PATH``), or None when unset/unreadable."""
    from openexecutive.config import get_settings

    settings = get_settings()
    path = settings.exec_outlook_credentials_path
    if path is None or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("integrations.outlook_client: unreadable exec credential file")
        return None
    exec_address = normalize_email(settings.exec_email_address)
    return _parse_credential(data, expect_email=exec_address, source=Path(path).name)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _email_address(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    addr = (entry.get("emailAddress") or {}) if isinstance(entry.get("emailAddress"), dict) else {}
    return normalize_email(str(addr.get("address") or ""))


def _addr_list(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [a for a in (_email_address(e) for e in entries) if a]


def _header_value(headers: object, name: str) -> str:
    if not isinstance(headers, list):
        return ""
    name = name.lower()
    for h in headers:
        if isinstance(h, dict) and str(h.get("name") or "").lower() == name:
            return str(h.get("value") or "")
    return ""


def _body_text(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    content = str(body.get("content") or "")
    content_type = str(body.get("contentType") or "text").lower()
    return html_to_text(content) if content_type == "html" else content.strip()


def parse_message(raw: dict[str, Any]) -> MailMessage:
    """A Graph ``message`` resource (with ``internetMessageHeaders``
    selected) as a ``MailMessage``."""
    headers = raw.get("internetMessageHeaders") or []
    frm = raw.get("from") or raw.get("sender") or {}
    frm_name = ""
    if isinstance(frm, dict):
        addr = frm.get("emailAddress")
        if isinstance(addr, dict):
            frm_name = str(addr.get("name") or "").strip()
    auto = _header_value(headers, "auto-submitted").strip().lower() not in ("", "no")
    return MailMessage(
        id=str(raw.get("id") or ""),
        thread_id=str(raw.get("conversationId") or ""),
        from_addr=_email_address(frm) if isinstance(frm, dict) else "",
        from_name=frm_name,
        to=_addr_list(raw.get("toRecipients")),
        cc=_addr_list(raw.get("ccRecipients")),
        reply_to=",".join(_addr_list(raw.get("replyTo"))),
        subject=str(raw.get("subject") or "").strip(),
        date=str(raw.get("receivedDateTime") or "").strip(),
        message_id_header=str(raw.get("internetMessageId") or "").strip(),
        references=_header_value(headers, "references").strip(),
        text=_body_text(raw.get("body")),
        labels=["DRAFT"] if raw.get("isDraft") else [],
        mailing_list=bool(_header_value(headers, "list-unsubscribe") or _header_value(headers, "list-id")),
        auto_generated=auto,
        ghostwritten=bool(_header_value(headers, GHOSTWRITTEN_HEADER.lower())),
    )


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _draft_payload(spec: DraftSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject": clean_header(spec.subject),
        "body": {"contentType": "Text", "content": spec.body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in spec.to],
    }
    if spec.cc:
        payload["ccRecipients"] = [{"emailAddress": {"address": a}} for a in spec.cc]
    payload["internetMessageHeaders"] = [{"name": GHOSTWRITTEN_HEADER, "value": "1"}]
    return payload


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


# Access tokens by a hash of (tenant, client_id, email, refresh_token, scopes)
# — never written anywhere.
_TOKENS: dict[str, tuple[str, float]] = {}


def _token_key(cred: GraphCredential, scopes: tuple[str, ...]) -> str:
    import hashlib

    return hashlib.sha256(
        f"{cred.tenant_id}\n{cred.client_id}\n{cred.email}\n{cred.refresh_token}\n{' '.join(scopes)}".encode()
    ).hexdigest()


class _GraphMailBase:
    """Shared Graph plumbing: token refresh, HTTP wrapper, and the
    read/draft operations both the Executive's own mailbox and a delegated
    (Act as me) mailbox use. ``transport`` is for tests (httpx MockTransport).
    """

    #: Scopes this instance requests on token refresh. Subclasses that need
    #: Mail.Send override this.
    _scopes: tuple[str, ...] = DELEGATED_SCOPES

    def __init__(
        self,
        email: str,
        *,
        credential: GraphCredential | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.email = normalize_email(email)
        self._credential = credential
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    def _cred(self) -> GraphCredential:
        cred = self._credential or self._load_credential()
        if cred is None:
            raise GraphNotConfigured(self.email)
        return cred

    def _load_credential(self) -> GraphCredential | None:
        return load_credential(self.email)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        cred = self._cred()
        key = _token_key(cred, self._scopes)
        cached = _TOKENS.get(key)
        if cached is not None and cached[1] - _TOKEN_SLACK_SECONDS > time.time():
            return cached[0]
        try:
            resp = await client.post(
                _token_uri(cred.tenant_id),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": cred.refresh_token,
                    "client_id": cred.client_id,
                    "client_secret": cred.client_secret,
                    "scope": " ".join(self._scopes),
                },
            )
        except httpx.HTTPError as exc:
            raise GraphError(f"token refresh failed: {type(exc).__name__}") from exc
        if resp.status_code in (400, 401):
            try:
                code = str(resp.json().get("error") or "")
            except ValueError:
                code = ""
            if code in ("invalid_grant", "unauthorized_client", "invalid_client") or resp.status_code == 401:
                raise GraphAuthError(code or "unauthorized")
        if resp.status_code >= 400:
            raise GraphError(f"token refresh returned {resp.status_code}")
        try:
            payload = resp.json()
            token = str(payload["access_token"])
            ttl = int(payload.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise GraphError("token refresh returned an unexpected response") from exc
        granted = str(payload.get("scope") or "")
        if granted and not set(self._scopes) <= set(granted.split()):
            raise GraphAuthError("missing_scope")
        _TOKENS[key] = (token, time.time() + ttl)
        return token

    async def _get(self, client: httpx.AsyncClient, path: str, params: Any = None) -> dict[str, Any]:
        return await self._request(client, "GET", path, params=params)

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._access_token(client)
        try:
            resp = await client.request(
                method,
                f"{GRAPH_BASE}{path}",
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise GraphError(f"graph {method} failed: {type(exc).__name__}") from exc
        if resp.status_code == 401:
            _TOKENS.pop(_token_key(self._cred(), self._scopes), None)
            raise GraphAuthError("unauthorized")
        if resp.status_code == 403:
            raise GraphAuthError("forbidden")
        if resp.status_code == 429:
            raise GraphError("graph rate limited")
        if resp.status_code >= 400:
            raise GraphError(f"graph {method} returned {resp.status_code}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise GraphError("graph returned a non-JSON response") from exc
        return data if isinstance(data, dict) else {}

    async def profile_email(self) -> str:
        """The address the credential opens, as Microsoft reports it."""
        async with self._client() as client:
            data = await self._get(client, "", {"$select": "mail,userPrincipalName"})
        address = data.get("mail") or data.get("userPrincipalName") or ""
        return normalize_email(str(address))

    async def search_threads(self, query: str, *, max_results: int = 5) -> list[ThreadSummary]:
        """Messages matching a Graph ``$search``, newest first: one summary
        per matching message's conversation (subject, sender, date only)."""
        async with self._client() as client:
            data = await self._get(
                client,
                "/messages",
                {
                    "$search": f'"{query[:300]}"',
                    "$top": max(1, min(max_results, 10)),
                    "$select": "id,conversationId,subject,from,receivedDateTime",
                },
            )
        summaries: list[ThreadSummary] = []
        seen: set[str] = set()
        for m in data.get("value") or []:
            if not isinstance(m, dict):
                continue
            conv = str(m.get("conversationId") or "")
            if not conv or conv in seen:
                continue
            seen.add(conv)
            frm = m.get("from") or {}
            frm_name = ""
            if isinstance(frm, dict) and isinstance(frm.get("emailAddress"), dict):
                frm_name = str(frm["emailAddress"].get("name") or "") or _email_address(frm)
            summaries.append(ThreadSummary(
                id=conv,
                subject=str(m.get("subject") or ""),
                sender=frm_name,
                date=str(m.get("receivedDateTime") or ""),
            ))
        return summaries

    async def get_thread(self, thread_id: str) -> MailThread:
        if not valid_id(thread_id):
            raise GraphError("invalid thread id")
        async with self._client() as client:
            data = await self._get(
                client,
                "/messages",
                {
                    "$filter": f"conversationId eq '{thread_id}'",
                    "$select": (
                        "id,conversationId,subject,from,toRecipients,ccRecipients,replyTo,isDraft,"
                        "receivedDateTime,internetMessageId,body,internetMessageHeaders"
                    ),
                    "$orderby": "receivedDateTime asc",
                },
            )
        messages = [parse_message(m) for m in data.get("value") or [] if isinstance(m, dict)]
        return MailThread(id=thread_id, messages=messages)

    async def list_sent(self, limit: int = 40) -> list[MailMessage]:
        """The mailbox's recent sent mail, newest first (at most ``limit``)."""
        async with self._client() as client:
            data = await self._get(
                client,
                "/mailFolders/sentitems/messages",
                {
                    "$top": max(1, min(limit, 100)),
                    "$orderby": "sentDateTime desc",
                    "$select": (
                        "id,conversationId,subject,from,toRecipients,ccRecipients,replyTo,isDraft,"
                        "receivedDateTime,internetMessageId,body,internetMessageHeaders"
                    ),
                },
            )
        return [parse_message(m) for m in data.get("value") or [] if isinstance(m, dict)]

    async def send_as_signature(self) -> str:
        """Graph has no API for a mailbox's configured signature (it lives in
        client apps, not the mailbox); we cannot read one the way Gmail's
        ``settings.sendAs`` exposes it. Callers fall back to no signature."""
        return ""

    async def create_draft(self, spec: DraftSpec) -> CreatedDraft:
        """Save ``spec`` as a draft. Nothing is sent."""
        if spec.thread_id is not None and not valid_id(spec.thread_id):
            raise GraphError("invalid thread id")
        payload = _draft_payload(spec)
        async with self._client() as client:
            if spec.thread_id:
                created = await self._request(
                    client, "POST", f"/messages/{spec.thread_id}/createReply", json_body={}
                )
                draft_id = str(created.get("id") or "")
                if not draft_id:
                    raise GraphError("createReply returned no draft id")
                data = await self._request(
                    client,
                    "PATCH",
                    f"/messages/{draft_id}",
                    json_body={k: v for k, v in payload.items() if k != "internetMessageHeaders"},
                )
                data.setdefault("id", draft_id)
            else:
                data = await self._request(client, "POST", "/messages", json_body=payload)
        return CreatedDraft(
            draft_id=str(data.get("id") or ""),
            message_id=str(data.get("id") or ""),
            thread_id=str(data.get("conversationId") or spec.thread_id or ""),
            web_link=str(data.get("webLink") or ""),
        )


class OutlookClient(_GraphMailBase):
    """The Executive's own Outlook mailbox: everything ``_GraphMailBase``
    offers, plus send/list-unread/mark-read for the poller and native tools.
    """

    _scopes: tuple[str, ...] = OWN_MAILBOX_SCOPES

    def _load_credential(self) -> GraphCredential | None:
        return load_exec_credential()

    async def list_unread(self, *, max_results: int = 25) -> list[MailMessage]:
        async with self._client() as client:
            data = await self._get(
                client,
                "/mailFolders/inbox/messages",
                {
                    "$filter": "isRead eq false",
                    "$top": max(1, min(max_results, 100)),
                    "$orderby": "receivedDateTime asc",
                    "$select": (
                        "id,conversationId,subject,from,toRecipients,ccRecipients,replyTo,isDraft,"
                        "receivedDateTime,internetMessageId,body,internetMessageHeaders"
                    ),
                },
            )
        return [parse_message(m) for m in data.get("value") or [] if isinstance(m, dict)]

    async def mark_read(self, message_id: str) -> None:
        if not valid_id(message_id):
            raise GraphError("invalid message id")
        async with self._client() as client:
            await self._request(client, "PATCH", f"/messages/{message_id}", json_body={"isRead": True})

    async def send(self, spec: DraftSpec) -> None:
        """Send ``spec`` immediately. Never used by a delegated mailbox —
        ``DelegateOutlook`` (delegation/outlook.py) has no access to this
        class."""
        message: dict[str, Any] = {
            "subject": clean_header(spec.subject),
            "body": {"contentType": "Text", "content": spec.body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in spec.to],
        }
        if spec.cc:
            message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in spec.cc]
        async with self._client() as client:
            if spec.thread_id and valid_id(spec.thread_id):
                await self._request(
                    client, "POST", f"/messages/{spec.thread_id}/reply",
                    json_body={"comment": spec.body, "message": message},
                )
            else:
                await self._request(
                    client, "POST", "/sendMail", json_body={"message": message, "saveToSentItems": True}
                )


def outlook_for(email: str) -> OutlookClient:
    """The client for the Executive's own Outlook mailbox."""
    return OutlookClient(email)
