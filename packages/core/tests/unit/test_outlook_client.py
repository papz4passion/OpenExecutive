"""The shared Microsoft Graph client (integrations/outlook_client.py) used by
both the Executive's own Outlook mailbox and Act as me."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from openexecutive.integrations import outlook_client as oc
from openexecutive.integrations.outlook_client import (
    DraftSpec,
    GraphAuthError,
    GraphCredential,
    GraphNotConfigured,
    OutlookClient,
    _GraphMailBase,
    credential_path,
    load_credential,
    parse_message,
)

EMAIL = "olivia@co.example"
TENANT = "contoso-tenant"


@pytest.fixture(autouse=True)
def _fresh_tokens() -> None:
    oc._TOKENS.clear()


def _cred() -> GraphCredential:
    return GraphCredential(
        email=EMAIL, refresh_token="r1", client_id="cid", client_secret="sec", tenant_id=TENANT
    )


# --------------------------------------------------------------------------- #
# Credential file
# --------------------------------------------------------------------------- #


def _write(directory: Path, content: object, *, name: str = EMAIL) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{oc.email_key(name)}.json").write_text(json.dumps(content))


def test_a_well_formed_credential_round_trips(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "email": EMAIL,
        "authorized_user": {
            "refresh_token": "r1", "client_id": "cid", "client_secret": "sec", "tenant_id": TENANT,
        },
    }
    directory = tmp_path / "creds"
    _write(directory, payload)
    assert credential_path(EMAIL, directory=directory).name == f"{oc.email_key(EMAIL)}.json"
    assert load_credential(EMAIL, directory=directory) == _cred()


@pytest.mark.parametrize("content", [
    "not json at all",
    {"email": "someone.else@co.example", "authorized_user": {
        "refresh_token": "r", "client_id": "c", "client_secret": "s", "tenant_id": TENANT}},
    {"email": EMAIL, "authorized_user": {"refresh_token": "", "client_id": "c", "client_secret": "s", "tenant_id": TENANT}},
    {"email": EMAIL},
    {"email": EMAIL, "authorized_user": {
        "refresh_token": "r", "client_id": "c", "client_secret": "s", "tenant_id": "../evil"}},
])
def test_a_bad_credential_file_is_ignored(tmp_path: Path, content: object) -> None:
    directory = tmp_path / "creds"
    if isinstance(content, str):
        directory.mkdir()
        (directory / f"{oc.email_key(EMAIL)}.json").write_text(content)
    else:
        _write(directory, content)
    assert load_credential(EMAIL, directory=directory) is None


def test_no_file_no_credential(tmp_path: Path) -> None:
    assert load_credential(EMAIL, directory=tmp_path) is None
    assert load_credential("", directory=tmp_path) is None


# --------------------------------------------------------------------------- #
# Message parsing
# --------------------------------------------------------------------------- #


def _message(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "m1",
        "conversationId": "t1",
        "subject": "Brand refresh pilot",
        "from": {"emailAddress": {"name": "Dana Prospect", "address": "Dana@NorthPeak.example"}},
        "toRecipients": [
            {"emailAddress": {"name": "Olivia", "address": "olivia@co.example"}},
            {"emailAddress": {"address": "ops@co.example"}},
        ],
        "ccRecipients": [{"emailAddress": {"address": "sam@northpeak.example"}}],
        "replyTo": [{"emailAddress": {"address": "billing@elsewhere.example"}}],
        "receivedDateTime": "2026-01-01T00:00:00Z",
        "internetMessageId": "<abc@mail.example>",
        "body": {"contentType": "text", "content": "Can we start Oct 5?"},
        "internetMessageHeaders": [
            {"name": "References", "value": "<prev@mail.example>"},
            {"name": "List-Unsubscribe", "value": "<mailto:x@y>"},
        ],
    }
    base.update(overrides)
    return base


def test_a_message_is_parsed_into_what_a_reply_needs() -> None:
    parsed = parse_message(_message())
    assert parsed.id == "m1"
    assert parsed.thread_id == "t1"
    assert parsed.from_addr == "dana@northpeak.example"
    assert parsed.from_name == "Dana Prospect"
    assert parsed.to == ["olivia@co.example", "ops@co.example"]
    assert parsed.cc == ["sam@northpeak.example"]
    assert parsed.reply_to == "billing@elsewhere.example"
    assert parsed.message_id_header == "<abc@mail.example>"
    assert parsed.references == "<prev@mail.example>"
    assert parsed.text == "Can we start Oct 5?"
    assert parsed.mailing_list is True


def test_html_only_mail_becomes_text() -> None:
    parsed = parse_message(_message(
        body={"contentType": "html", "content": "<div>Hello<br>there</div><p>&amp; bye</p>"},
        internetMessageHeaders=[{"name": "Auto-Submitted", "value": "auto-replied"}],
    ))
    assert parsed.text.splitlines() == ["Hello", "there", "& bye"]
    assert parsed.auto_generated is True


def test_ghostwritten_header_is_detected() -> None:
    parsed = parse_message(_message(
        internetMessageHeaders=[{"name": oc.GHOSTWRITTEN_HEADER, "value": "1"}],
    ))
    assert parsed.ghostwritten is True


def test_draft_message_carries_a_draft_label() -> None:
    """delegation_tools.py filters thread messages on `"DRAFT" not in
    m.labels` (Gmail-derived) — Outlook messages must carry the same
    signal, from Graph's isDraft."""
    assert parse_message(_message(isDraft=True)).labels == ["DRAFT"]
    assert parse_message(_message(isDraft=False)).labels == []
    assert parse_message(_message()).labels == []


def test_missing_fields_do_not_crash() -> None:
    parsed = parse_message({"id": "m2"})
    assert parsed.id == "m2"
    assert parsed.thread_id == ""
    assert parsed.from_addr == ""
    assert parsed.to == []
    assert parsed.text == ""


# --------------------------------------------------------------------------- #
# The client, against a mocked Graph
# --------------------------------------------------------------------------- #


class FakeGraph:
    def __init__(self, *, token_status: int = 200, token_error: str = "",
                 scope: str = " ".join(oc.OWN_MAILBOX_SCOPES), api_status: int = 200) -> None:
        self.token_status = token_status
        self.token_error = token_error
        self.scope = scope
        self.api_status = api_status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "login.microsoftonline.com" in request.url.host:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": self.token_error})
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600, "scope": self.scope})
        assert request.headers["Authorization"] == "Bearer at"
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={})
        path = request.url.path
        if path.endswith("/me"):
            return httpx.Response(200, json={"mail": "Olivia@Co.Example"})
        if path.endswith("/messages") and request.method == "POST":
            return httpx.Response(200, json={"id": "m9", "conversationId": "t1"})
        return httpx.Response(404, json={})

    def client(self, cls: type[_GraphMailBase] = OutlookClient) -> _GraphMailBase:
        return cls(EMAIL, credential=_cred(), transport=httpx.MockTransport(self.handler))


def test_the_token_is_refreshed_once_and_reused() -> None:
    graph = FakeGraph()
    client = graph.client()
    assert asyncio.run(client.profile_email()) == EMAIL
    assert asyncio.run(client.profile_email()) == EMAIL
    token_calls = [r for r in graph.requests if "login.microsoftonline.com" in r.url.host]
    assert len(token_calls) == 1
    assert b"grant_type=refresh_token" in token_calls[0].content


def test_a_draft_is_created_without_a_thread() -> None:
    graph = FakeGraph()
    created = asyncio.run(graph.client().create_draft(DraftSpec(
        to=["dana@northpeak.example"], subject="Re: Pilot", body="Yes.",
    )))
    assert created.draft_id == "m9"
    assert created.thread_id == "t1"
    post = next(r for r in graph.requests if r.method == "POST" and r.url.path.endswith("/messages"))
    sent = json.loads(post.content)
    assert sent["toRecipients"] == [{"emailAddress": {"address": "dana@northpeak.example"}}]


@pytest.mark.parametrize("graph", [
    FakeGraph(token_status=400, token_error="invalid_grant"),
    FakeGraph(scope="https://graph.microsoft.com/Mail.Read"),
    FakeGraph(api_status=401),
    FakeGraph(api_status=403),
])
def test_microsoft_refusing_the_credential_is_an_auth_error(graph: FakeGraph) -> None:
    with pytest.raises(GraphAuthError):
        asyncio.run(graph.client().profile_email())


def test_a_429_is_not_an_auth_error() -> None:
    graph = FakeGraph(api_status=429)
    with pytest.raises(oc.GraphError) as err:
        asyncio.run(graph.client().profile_email())
    assert not isinstance(err.value, GraphAuthError)


def test_no_credential_is_not_configured() -> None:
    with pytest.raises(GraphNotConfigured):
        asyncio.run(OutlookClient(EMAIL).profile_email())
