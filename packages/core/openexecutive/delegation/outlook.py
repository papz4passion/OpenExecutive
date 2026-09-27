"""A direct Outlook client for one person's own mailbox (Act as me).

Mirrors ``delegation/gmail.py`` exactly: read + draft only, a per-person
credential in ``DELEGATION_OUTLOOK_CREDENTIALS_DIR`` (never the Executive's
own ``EXEC_OUTLOOK_CREDENTIALS_PATH``), and identity re-verified on every
use. ``DelegateOutlook`` subclasses ``integrations.outlook_client``'s
``_GraphMailBase`` directly — never ``OutlookClient`` — so there is no send
method anywhere in its MRO (a unit test asserts this via introspection, the
same guarantee Gmail's ``DelegateGmail`` gives via its own unit test).

``_GraphMailBase``'s default credential lookup (``load_credential(self.email)``,
reading ``DELEGATION_OUTLOOK_CREDENTIALS_DIR``) is already the delegated
lookup, so nothing needs to be overridden here.
"""
from __future__ import annotations

import logging
from typing import Literal

from openexecutive.delegation.gmail import normalize_email
from openexecutive.integrations.outlook_client import (
    GraphAuthError,
    GraphNotConfigured,
    _GraphMailBase,
)

logger = logging.getLogger(__name__)

OutlookStatus = Literal[
    "connected",
    "not_configured",
    "needs_reconnect",
    "mismatch",
    "no_email",
    "shared_mailbox",
    "error",
]

STATUS_MESSAGES: dict[str, str] = {
    "connected": "Connected.",
    "not_configured": (
        "Your Outlook isn't connected. Run scripts/connect-own-outlook.py as yourself "
        "(Settings → Act as me shows how)."
    ),
    "needs_reconnect": (
        "Microsoft no longer accepts the saved sign-in for your Outlook. Connect it "
        "again with scripts/connect-own-outlook.py."
    ),
    "mismatch": (
        "The connected Outlook isn't the address on your People entry. Connect that "
        "account, or correct your email on the People page."
    ),
    "no_email": "Your People entry has no email address. Add it on the People page first.",
    "shared_mailbox": (
        "Your address is the Executive's own mailbox, so it can't write as you from "
        "it. Give the Executive a Microsoft account of its own first."
    ),
    "error": "Couldn't reach your Outlook just now. Try again in a moment.",
}


class DelegateOutlook(_GraphMailBase):
    """One person's Outlook mailbox (Act as me): read + draft only."""


def outlook_for(email: str) -> DelegateOutlook:
    """The client for ``email``'s own delegated mailbox."""
    return DelegateOutlook(email)


async def outlook_status(person_email: str | None, *, outlook: object = None) -> OutlookStatus:
    """Whether ``person_email``'s own mailbox can be used right now. Asks
    Microsoft which mailbox the credential opens. Never raises. Mirrors
    ``delegation.gmail.gmail_status``."""
    from openexecutive.config import get_settings

    address = normalize_email(person_email)
    if not address:
        return "no_email"
    try:
        exec_address = normalize_email(get_settings().exec_email_address)
    except Exception:
        logger.warning("delegation.outlook: settings unreadable — refusing", exc_info=True)
        return "error"
    if address == exec_address:
        return "shared_mailbox"
    client = outlook if outlook is not None else outlook_for(address)
    try:
        opened = await client.profile_email()  # type: ignore[attr-defined]
    except GraphNotConfigured:
        return "not_configured"
    except GraphAuthError:
        return "needs_reconnect"
    except Exception:
        logger.warning("delegation.outlook: status check failed", exc_info=True)
        return "error"
    return "connected" if opened == address else "mismatch"
