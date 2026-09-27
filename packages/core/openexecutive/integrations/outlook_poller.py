"""Microsoft Graph polling loop for the Executive's own Outlook mailbox.

Mirrors ``integrations/email_poller.py``'s behavior — no roster gate on
inbound, a ``[POLICY]`` notice for non-roster/contact senders, automated and
self-sent senders filtered, pause respected, the message marked read after
handling — but Graph's ``GET /me/mailFolders/inbox/messages`` returns
structured JSON directly, so none of Gmail's marker-based text parsing
(``_split_gmail_content``, ``_attachment_line``, etc.) is needed. The
quote-stripping / forwarded-message detection (``_new_text_lines``) and the
policy-notice text (``_contact_notice``, ``_forwarded_by_principal_notice``,
``_one_line``) are provider-agnostic and reused as-is from
``email_poller.py``.

Document attachments are not read for Outlook mail in this phase — Graph's
attachment API needs its own reader the way ``email_attachments.py`` wraps
Gmail's, which is out of scope here (not required by the Outlook inbound
spec).

Redirect-steering headers (``Reply-To`` and friends): unlike Gmail's raw MIME
text, the message text handed to the Executive here is built from structured
fields this module chooses what to include from — ``replyTo`` is parsed onto
``MailMessage.reply_to`` but never included in the turn text, so there is
nothing to strip.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from openexecutive.config import get_settings
from openexecutive.integrations.email_poller import (
    _REPLY_SUBJECT_RE,
    _contact_notice,
    _forwarded_by_principal_notice,
    _new_text_lines,
)
from openexecutive.integrations.outlook_client import MailMessage, OutlookClient

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = get_settings().email_poll_interval_seconds

# Prevents reprocessing the same message within a run (cleared on restart).
_processed_ids: set[str] = set()

_SKIP_SENDERS = ("noreply", "no-reply", "mailer-daemon", "postmaster", "do-not-reply")


def _forwarded(message: MailMessage) -> bool:
    """Whether the message carries a forwarded one below the sender's text."""
    return _new_text_lines(message.text.splitlines())[1]


def _private_to_principal_mail(from_addr: str, message: MailMessage) -> bool:
    """Whether this mail's turn is private to the principal — mail from one
    of their contacts, or mail the principal forwarded. Mirrors
    ``email_poller._private_to_principal_mail``."""
    if not from_addr:
        return False
    from openexecutive.people.store import find_person_by_email

    person = find_person_by_email(from_addr)
    if person is None:
        return find_person_by_email(from_addr, include_contacts=True) is not None
    return person.is_principal is True and _forwarded(message)


def _outlook_memory_text(message: MailMessage) -> str:
    """What peer memory should record as the sender's own words — the
    sender's new text plus subject (unless it's a reply/forward subject) and
    a forwarded marker. Mirrors ``email_poller._email_memory_text``."""
    new_lines, forwarded = _new_text_lines(message.text.splitlines())
    new_text = "\n".join(new_lines).strip()
    parts: list[str] = []
    if message.subject and not _REPLY_SUBJECT_RE.match(message.subject):
        parts.append(f"Subject: {message.subject}")
    if new_text:
        parts.append(new_text)
    if forwarded:
        parts.append("[Forwarded an earlier message]")
    return "\n\n".join(parts)


def _message_text(message: MailMessage) -> str:
    """The turn text the Executive sees for this message — built from
    structured fields, so redirect-steering headers (Reply-To) are simply
    never included."""
    lines = [f"From: {message.from_name} <{message.from_addr}>" if message.from_name else f"From: {message.from_addr}"]
    if message.to:
        lines.append(f"To: {', '.join(message.to)}")
    if message.cc:
        lines.append(f"Cc: {', '.join(message.cc)}")
    lines.append(f"Subject: {message.subject}")
    lines.append(f"Date: {message.date}")
    lines.append("")
    lines.append(message.text)
    return "\n".join(lines)


async def poll_once(client: OutlookClient) -> None:
    """One poll cycle: find unread messages, hand each to the Executive."""
    try:
        messages = await client.list_unread(max_results=10)
    except Exception:
        logger.exception("outlook: list_unread failed")
        return

    logger.debug("outlook poll cycle — %d unread message(s)", len(messages))
    settings = get_settings()
    for message in messages:
        if not message.id or message.id in _processed_ids:
            continue
        try:
            await _handle_email(client, message, settings.exec_email_address)
            _processed_ids.add(message.id)
        except Exception:
            logger.exception("outlook: failed for message=%s", message.id)


async def _handle_email(client: OutlookClient, message: MailMessage, user_email: str) -> None:
    # One message, one turn: no session bound while this message's own rows
    # are written (see email_poller._handle_email for why).
    from openexecutive.orchestrator.schedule_tools import set_session

    with set_session(None):
        await _handle_one_email(client, message, user_email)


async def _handle_one_email(client: OutlookClient, message: MailMessage, user_email: str) -> None:
    from_addr = message.from_addr
    if from_addr and from_addr.lower() == user_email.lower():
        logger.debug("outlook: skipping self-addressed message=%s", message.id)
        await _mark_read(client, message.id)
        return
    haystack = f"{message.from_name} {from_addr}".lower()
    if any(p in haystack for p in _SKIP_SENDERS):
        logger.debug("outlook: skipping automated sender for message=%s", message.id)
        await _mark_read(client, message.id)
        return

    from openexecutive.audit import log_event as audit_log
    from openexecutive.audit import private_rows
    from openexecutive.people.store import find_person_by_email

    sender_in_roster = find_person_by_email(from_addr) is not None
    try:
        private = _private_to_principal_mail(from_addr, message)
    except Exception:
        logger.exception(
            "outlook: could not tell whether message=%s is private — its audit rows are kept private",
            message.id,
        )
        private = True

    with private_rows(private):
        if not sender_in_roster:
            logger.info(
                "outlook: non-roster sender=%s message=%s — routing to Executive (no auto-reply allowed)",
                from_addr, message.id,
            )
            audit_log(
                "integration_inbound",
                f"Accepted non-roster Outlook email from {from_addr} (reply blocked at outbound gate)",
                actor="outlook",
                details={
                    "channel": "outlook",
                    "from": from_addr,
                    "message_id": message.id,
                    "outcome": "accepted_non_roster",
                },
                private=private,
            )

        logger.info("outlook: routing message=%s to Executive", message.id)
        session_id = f"outlook:{message.thread_id or from_addr}"
        audit_log(
            "integration_inbound",
            f"Inbound Outlook email from {from_addr}: {message.subject}" if message.subject
            else f"Inbound Outlook email from {from_addr}",
            actor="outlook",
            session_id=session_id,
            details={
                "channel": "outlook",
                "message_id": message.id,
                "thread_id": message.thread_id,
                "from": from_addr,
                "subject": message.subject,
            },
            private=private,
        )
        try:
            await _run_executive(message, from_addr, session_id)
        except Exception:
            logger.exception("outlook: Executive raised for message=%s", message.id)

        await _mark_read(client, message.id)


async def _run_executive(
    message: MailMessage,
    from_addr: str,
    session_id: str,
) -> None:
    from openexecutive.knowledge.retriever import retrieve
    from openexecutive.memory.episodic import format_for_prompt
    from openexecutive.onboarding.profile_builder import load_or_create_profile
    from openexecutive.orchestrator.executive import Executive
    from openexecutive.orchestrator.session import Session
    from openexecutive.people.store import find_person_by_email

    profile = load_or_create_profile()
    session = Session(
        company_profile=profile if not profile.is_empty() else None,
        session_id=session_id,
    )
    settings = get_settings()
    if from_addr and (
        from_addr.lower() == settings.exec_email_address.lower()
        or find_person_by_email(from_addr) is not None
    ):
        session.seen_channel_refs.add(("outlook", f"{from_addr}|{message.thread_id}"))
        session.seen_channel_refs.add(("outlook", from_addr))

    person_id: int | None = None
    person: Any = None
    contact: Any = None
    if from_addr:
        person = find_person_by_email(from_addr)
        person_id = person.id if person else None
        if person is None:
            contact = find_person_by_email(from_addr, include_contacts=True)

    co_present_person_ids: list[int] = []
    try:
        exec_email = settings.exec_email_address.lower()
        from_addr_lower = from_addr.lower()
        for addr in message.to + message.cc:
            addr_lower = addr.lower()
            if addr_lower in (exec_email, from_addr_lower):
                continue
            other = find_person_by_email(addr)
            if other and other.id is not None and other.id not in co_present_person_ids:
                co_present_person_ids.append(other.id)
    except Exception:
        logger.warning(
            "outlook: recipient parsing failed for message=%s — passing empty co-present list",
            message.id, exc_info=True,
        )

    policy_notice = ""
    if from_addr and person_id is None and contact is not None:
        policy_notice = _contact_notice(from_addr, contact)
        session.private_to_principal = True
    elif from_addr and person_id is None:
        policy_notice = (
            f"[POLICY] This inbound is from {from_addr}, who is NOT on your team's "
            "People roster. You can classify it, log a decision, schedule an internal "
            "follow-up, alert the principal, or surface a proposal to add the sender "
            "to the roster. You cannot send an outbound reply directly to "
            f"{from_addr} — the outbound gate will block it. To actually reply, the "
            "principal must add the sender to the People roster first.\n\n"
            "---\n\n"
        )
    elif getattr(person, "is_principal", False) is True and _forwarded(message):
        policy_notice = _forwarded_by_principal_notice(person)
        session.private_to_principal = True

    base_message = (
        f"You have an inbound Outlook email (message_id={message.id}, thread_id={message.thread_id}).\n\n"
        f"{policy_notice}{_message_text(message)}"
    )
    if from_addr:
        from openexecutive.integrations.inbound_hydration import hydrate_user_message

        base_message = hydrate_user_message(
            channel="outlook",
            channel_ref=from_addr.lower(),
            user_message=base_message,
        )

    executive = Executive()
    await executive.chat(
        user_message=base_message,
        session=session,
        retrieved_context=retrieve(query=message.text[:500]),
        episodic_context=format_for_prompt(),
        person_id=person_id,
        co_present_person_ids=co_present_person_ids or None,
        memory_text=_outlook_memory_text(message) if person_id is not None else None,
    )


async def _mark_read(client: OutlookClient, message_id: str) -> None:
    try:
        await client.mark_read(message_id)
        logger.debug("outlook: marked message=%s as read", message_id)
    except Exception:
        logger.warning("outlook: failed to mark message=%s as read", message_id)


async def run_outlook_poller(client: OutlookClient) -> None:
    """Async polling loop. Run as a background task; cancelled on shutdown."""
    from openexecutive.scheduler.pause import is_paused

    logger.info("outlook: started (interval=%ds)", POLL_INTERVAL_SECONDS)
    holding_for_pause = False
    while True:
        try:
            if is_paused():
                if not holding_for_pause:
                    logger.warning("executive paused — not polling Outlook")
                    holding_for_pause = True
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            if holding_for_pause:
                logger.info("executive resumed — polling Outlook again")
                holding_for_pause = False
            await poll_once(client)
        except asyncio.CancelledError:
            logger.info("outlook: cancelled")
            raise
        except Exception:
            logger.exception("outlook: unexpected error in poll cycle")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
