"""Outlook polling loop for the Executive's own mailbox
(integrations/outlook_poller.py) — mirrors test_email_poller_allowlist.py's
coverage for the Graph-backed poller."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import openexecutive.integrations.outlook_poller as poller
from openexecutive.integrations.outlook_client import MailMessage
from openexecutive.people import store as people_store

EXEC_ADDR = "exec@example.com"


@pytest.fixture(autouse=True)
def isolated_people_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "people.db"
    monkeypatch.setattr(people_store, "DB_PATH", db_path)
    people_store.initialize_db()
    return db_path


def _message(**overrides: Any) -> MailMessage:
    base = dict(
        id="m1", thread_id="t1", from_addr="alice@example.com", from_name="Alice",
        to=[EXEC_ADDR], subject="Hello", text="Body text here.",
        reply_to="billing@elsewhere.example",
    )
    base.update(overrides)
    return MailMessage(**base)


def _run(message: MailMessage) -> tuple[AsyncMock, AsyncMock]:
    client = AsyncMock()
    with (
        patch.object(poller, "_run_executive", new=AsyncMock()) as run_exec,
        patch.object(poller, "_mark_read", new=AsyncMock()) as mark_read,
    ):
        asyncio.run(poller._handle_email(client, message, EXEC_ADDR))
    return run_exec, mark_read


def test_known_sender_routes_to_executive() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    run_exec, mark_read = _run(_message())
    assert run_exec.await_count == 1
    assert mark_read.await_count == 1


def test_unknown_sender_still_routes_to_executive() -> None:
    people_store.upsert_person(full_name="Someone Else", email="bob@example.com")
    run_exec, mark_read = _run(_message(from_addr="stranger@example.com"))
    assert run_exec.await_count == 1
    assert mark_read.await_count == 1


def test_self_addressed_message_is_skipped() -> None:
    run_exec, mark_read = _run(_message(from_addr=EXEC_ADDR))
    assert run_exec.await_count == 0
    assert mark_read.await_count == 1  # still marked read so it doesn't loop


@pytest.mark.parametrize("addr", ["noreply@example.com", "no-reply@example.com", "mailer-daemon@example.com"])
def test_automated_sender_is_skipped(addr: str) -> None:
    run_exec, mark_read = _run(_message(from_addr=addr))
    assert run_exec.await_count == 0
    assert mark_read.await_count == 1


# --------------------------------------------------------------------------- #
# Pause holds the inbox untouched
# --------------------------------------------------------------------------- #


def test_pause_holds_the_inbox_untouched() -> None:
    client = AsyncMock()
    client.list_unread = AsyncMock(return_value=[_message()])
    with (
        patch("openexecutive.scheduler.pause.is_paused", return_value=True),
        patch.object(poller, "poll_once", new=AsyncMock()) as poll_once,
        patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(poller.run_outlook_poller(client))
    poll_once.assert_not_called()
    client.list_unread.assert_not_called()
    client.mark_read.assert_not_called()


# --------------------------------------------------------------------------- #
# Redirect-steering: reply_to is parsed but never surfaced to the Executive
# --------------------------------------------------------------------------- #


def test_reply_to_never_reaches_the_message_text() -> None:
    text = poller._message_text(_message(reply_to="attacker-controlled@evil.example"))
    assert "attacker-controlled@evil.example" not in text


# --------------------------------------------------------------------------- #
# _run_executive: policy notice + private session
# --------------------------------------------------------------------------- #


def _capture_run_executive(message: MailMessage) -> tuple[str, Any]:
    captured: dict[str, Any] = {}

    class _StubExecutive:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def chat(self, **kwargs: Any) -> Any:
            captured["user_message"] = kwargs.get("user_message", "")
            captured["session"] = kwargs.get("session")
            return None

    from openexecutive.memory import company_profile as _cp
    empty_profile = _cp.CompanyProfile()

    with (
        patch("openexecutive.orchestrator.executive.Executive", _StubExecutive),
        patch("openexecutive.onboarding.profile_builder.load_or_create_profile", return_value=empty_profile),
        patch("openexecutive.knowledge.retriever.retrieve", return_value=""),
        patch("openexecutive.memory.episodic.format_for_prompt", return_value=""),
    ):
        asyncio.run(poller._run_executive(message, message.from_addr, f"outlook:{message.thread_id}"))
    return captured.get("user_message", ""), captured.get("session")


def test_unrostered_sender_gets_policy_notice() -> None:
    message = _message(from_addr="stranger@example.com", subject="Cold inbound")
    user_message, _session = _capture_run_executive(message)
    assert "[POLICY]" in user_message
    assert "stranger@example.com" in user_message
    assert "outbound" in user_message.lower()


def test_rostered_sender_gets_no_policy_notice() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    message = _message(from_addr="alice@example.com")
    user_message, _session = _capture_run_executive(message)
    assert "[POLICY]" not in user_message


def test_contact_mail_marks_the_session_private() -> None:
    people_store.upsert_person(
        full_name="Carol Contact", email="carol@partner.example", kind="contact",
    )
    message = _message(from_addr="carol@partner.example")
    _user_message, session = _capture_run_executive(message)
    assert session is not None
    assert session.private_to_principal is True
