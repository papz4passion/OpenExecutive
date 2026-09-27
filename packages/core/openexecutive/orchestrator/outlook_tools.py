"""Native (non-MCP) tools for the Executive's own Outlook mailbox.

There is no Outlook MCP server the way ``google_workspace`` is for Gmail
(see design.md decision 1), so these tools call ``integrations.outlook_client``
directly. The roster gate Gmail gets "for free" via
``mcp_gateway._check_gmail_recipients`` is implemented here as an explicit
check every tool calls first (``_check_recipients``) — the same security
property, enforced at a different layer because there is no MCP gateway call
to intercept.

Unconfigured Outlook (any of the three ``MICROSOFT_OAUTH_*`` settings, or no
``EXEC_OUTLOOK_CREDENTIALS_PATH``, absent) is not a reason to hide these tools
from the model — the tool list stays static and sorted either way, per the
prompt-caching invariant (CLAUDE.md). Each handler checks configuration at
call time and returns a plain error result instead of touching Graph.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openexecutive.integrations.outlook_client import (
    DraftSpec,
    GraphError,
    GraphNotConfigured,
    outlook_for,
)

logger = logging.getLogger(__name__)

_RECIPIENT_FIELDS = ("to", "cc")


def _configured_error(tool: str) -> str:
    return json.dumps({"error": (
        f"Outlook is not configured for the Executive's own mailbox — {tool} "
        "is unavailable. An operator must set MICROSOFT_OAUTH_CLIENT_ID/"
        "_CLIENT_SECRET/_TENANT_ID and EXEC_OUTLOOK_CREDENTIALS_PATH (see "
        "docs/outlook_setup.md) before Outlook mail can be sent."
    )})


def _check_recipients(tool: str, arguments: dict[str, Any]) -> str | None:
    """Return None if every recipient resolves to the People roster (or the
    Executive's own address), else an error result and an audit row.

    Reuses ``mcp_gateway._roster_allow_set`` — the same egress allow-list
    Gmail, Calendar, and Drive share, including its contact-egress gating
    (a contact is reachable only on a turn the principal started on a
    verified surface, or an explicit grant) — so this stays in lockstep with
    the other channels rather than drifting into a second implementation.
    """
    from email.utils import getaddresses

    from openexecutive.orchestrator.mcp_gateway import _roster_allow_set

    allow = _roster_allow_set()
    for field in _RECIPIENT_FIELDS:
        value = arguments.get(field)
        if not value:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, str):
                return _blocked(tool, field, f"<non-string:{type(item).__name__}>")
            if "\n" in item or "\r" in item:
                return _blocked(tool, field, "<contains-newline>")
        parsed = getaddresses([s for s in items if isinstance(s, str)])
        if not parsed or any(not addr for _name, addr in parsed):
            return _blocked(tool, field, "<unparseable>")
        for _name, addr in parsed:
            if addr.lower() not in allow:
                return _blocked(tool, field, addr)
    return None


def _blocked(tool: str, field: str, addr: str) -> str:
    from openexecutive.audit import log_event as audit_log

    logger.warning(
        "blocked outbound outlook send: tool=%s field=%s addr=%s not in allow-list",
        tool, field, addr,
    )
    audit_log(
        "integration_outbound_blocked",
        f"Blocked outbound Outlook email to {addr} (tool={tool} field={field})",
        actor="outlook_tools",
        details={"tool": tool, "field": field, "address": addr, "channel": "outlook"},
    )
    return json.dumps({"error": (
        f"recipient {addr!r} in field {field!r} is not on the People roster or "
        "the Executive's own address — refusing to send."
    )})


def _spec_from_arguments(arguments: dict[str, Any], *, thread_id: str | None = None) -> DraftSpec:
    return DraftSpec(
        to=[a for a in arguments.get("to") or [] if isinstance(a, str)],
        cc=[a for a in arguments.get("cc") or [] if isinstance(a, str)],
        subject=str(arguments.get("subject") or ""),
        body=str(arguments.get("body") or ""),
        thread_id=thread_id,
    )


def _exec_client() -> Any:
    from openexecutive.config import get_settings

    return outlook_for(get_settings().exec_email_address)


async def handle_send_outlook_email(arguments: dict[str, Any]) -> str:
    blocked = _check_recipients("send_outlook_email", arguments)
    if blocked is not None:
        return blocked
    try:
        await _exec_client().send(_spec_from_arguments(arguments))
    except GraphNotConfigured:
        return _configured_error("send_outlook_email")
    except GraphError as exc:
        return json.dumps({"error": f"Outlook send failed: {exc}"})
    return json.dumps({"sent": True, "to": arguments.get("to"), "subject": arguments.get("subject")})


async def handle_reply_outlook_email(arguments: dict[str, Any]) -> str:
    blocked = _check_recipients("reply_outlook_email", arguments)
    if blocked is not None:
        return blocked
    message_id = arguments.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        return json.dumps({"error": "message_id is required to reply."})
    try:
        await _exec_client().send(_spec_from_arguments(arguments, thread_id=message_id))
    except GraphNotConfigured:
        return _configured_error("reply_outlook_email")
    except GraphError as exc:
        return json.dumps({"error": f"Outlook reply failed: {exc}"})
    return json.dumps({"sent": True, "message_id": message_id, "to": arguments.get("to")})


async def handle_draft_outlook_email(arguments: dict[str, Any]) -> str:
    blocked = _check_recipients("draft_outlook_email", arguments)
    if blocked is not None:
        return blocked
    message_id = arguments.get("message_id")
    thread_id = message_id if isinstance(message_id, str) and message_id else None
    try:
        created = await _exec_client().create_draft(_spec_from_arguments(arguments, thread_id=thread_id))
    except GraphNotConfigured:
        return _configured_error("draft_outlook_email")
    except GraphError as exc:
        return json.dumps({"error": f"Outlook draft failed: {exc}"})
    return json.dumps({"draft_id": created.draft_id, "thread_id": created.thread_id})


_RECIPIENT_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Email addresses. Each must be on the People roster or the Executive's own address.",
}

SEND_OUTLOOK_EMAIL_TOOL: dict[str, Any] = {
    "name": "send_outlook_email",
    "description": (
        "Send a new email through the Executive's own Outlook mailbox. Every "
        "recipient (to/cc) must be on the People roster or the Executive's "
        "own address — anything else is refused before Outlook is called."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": _RECIPIENT_SCHEMA,
            "cc": _RECIPIENT_SCHEMA,
            "subject": {"type": "string", "description": "Email subject (max 200 chars)"},
            "body": {"type": "string", "description": "Plain-text email body"},
        },
        "required": ["to", "subject", "body"],
    },
}

REPLY_OUTLOOK_EMAIL_TOOL: dict[str, Any] = {
    "name": "reply_outlook_email",
    "description": (
        "Reply to an existing message in the Executive's own Outlook mailbox "
        "(threaded via Graph's createReply). Every recipient (to/cc) must be "
        "on the People roster or the Executive's own address."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "The Graph message id being replied to"},
            "to": _RECIPIENT_SCHEMA,
            "cc": _RECIPIENT_SCHEMA,
            "body": {"type": "string", "description": "Plain-text reply body"},
        },
        "required": ["message_id", "to", "body"],
    },
}

DRAFT_OUTLOOK_EMAIL_TOOL: dict[str, Any] = {
    "name": "draft_outlook_email",
    "description": (
        "Save a draft in the Executive's own Outlook mailbox without sending "
        "it — a new draft, or (with message_id) a threaded reply draft. "
        "Every recipient (to/cc) must be on the People roster or the "
        "Executive's own address."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": _RECIPIENT_SCHEMA,
            "cc": _RECIPIENT_SCHEMA,
            "subject": {"type": "string", "description": "Email subject (ignored when message_id is set)"},
            "body": {"type": "string", "description": "Plain-text draft body"},
            "message_id": {
                "type": "string",
                "description": "Optional: the Graph message id to draft a threaded reply to",
            },
        },
        "required": ["to", "body"],
    },
}

OUTLOOK_TOOLS: list[dict[str, Any]] = [
    SEND_OUTLOOK_EMAIL_TOOL,
    REPLY_OUTLOOK_EMAIL_TOOL,
    DRAFT_OUTLOOK_EMAIL_TOOL,
]

OUTLOOK_TOOL_HANDLERS: dict[str, Any] = {
    "send_outlook_email": handle_send_outlook_email,
    "reply_outlook_email": handle_reply_outlook_email,
    "draft_outlook_email": handle_draft_outlook_email,
}

# Every tool above that accepts recipients must call _check_recipients first —
# enumerated so a unit test can enforce it even as tools are added later.
RECIPIENT_TOOL_NAMES: frozenset[str] = frozenset(
    {"send_outlook_email", "reply_outlook_email", "draft_outlook_email"}
)
