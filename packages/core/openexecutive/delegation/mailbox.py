"""Resolve which provider (Gmail or Outlook) backs a person's Act as me
mailbox (design.md decision 3).

A person connects at most one — which credential file exists decides, not a
stored "provider" column, to avoid a schema migration. If both happen to
exist for one person (shouldn't normally happen: each connect script writes
to its own directory, and there is no UI path that connects both), Gmail
wins for backward compatibility and a warning is logged.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

MailboxStatus = Literal[
    "connected",
    "not_configured",
    "needs_reconnect",
    "mismatch",
    "no_email",
    "shared_mailbox",
    "error",
]


def mailbox_for(email: str) -> Any | None:
    """The Act as me client for ``email`` — whichever provider is connected,
    or ``None`` when neither is."""
    from openexecutive.delegation.gmail import gmail_for, normalize_email
    from openexecutive.delegation.gmail import load_credential as _load_gmail
    from openexecutive.delegation.outlook import outlook_for
    from openexecutive.integrations.outlook_client import load_credential as _load_outlook

    address = normalize_email(email)
    if not address:
        return None
    has_gmail = _load_gmail(address) is not None
    has_outlook = _load_outlook(address) is not None
    if has_gmail and has_outlook:
        logger.warning(
            "delegation.mailbox: %s has both a Gmail and an Outlook credential — Gmail wins",
            address,
        )
        return gmail_for(address)
    if has_gmail:
        return gmail_for(address)
    if has_outlook:
        return outlook_for(address)
    return None


async def mailbox_status(person_email: str | None, *, mailbox: Any = None) -> MailboxStatus:
    """Whether ``person_email``'s Act as me mailbox (whichever provider) can
    be used right now. Never raises."""
    from openexecutive.delegation.gmail import gmail_status, normalize_email
    from openexecutive.delegation.outlook import DelegateOutlook, outlook_status

    client = mailbox if mailbox is not None else mailbox_for(person_email or "")
    if client is None:
        return "no_email" if not normalize_email(person_email) else "not_configured"
    if isinstance(client, DelegateOutlook):
        return await outlook_status(person_email, outlook=client)
    return await gmail_status(person_email, gmail=client)
