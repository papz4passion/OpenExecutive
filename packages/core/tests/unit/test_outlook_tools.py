"""Native (non-MCP) send/reply/draft tools for the Executive's own Outlook
mailbox (orchestrator/outlook_tools.py) — the roster gate every one of them
must apply before any Graph call. Handlers return a JSON string, matching
every other tool handler in this codebase (see executive.py's
"handler always returns JSON today")."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import openexecutive.orchestrator.outlook_tools as ot
from openexecutive.config import get_settings
from openexecutive.integrations.outlook_client import CreatedDraft
from openexecutive.people import store as people_store

EXEC_ADDR = get_settings().exec_email_address  # tests/conftest.py's EXEC_EMAIL_ADDRESS


@pytest.fixture(autouse=True)
def isolated_people_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "people.db"
    monkeypatch.setattr(people_store, "DB_PATH", db_path)
    people_store.initialize_db()
    return db_path


def _run(coro: Any) -> dict[str, Any]:
    return json.loads(asyncio.run(coro))


# --------------------------------------------------------------------------- #
# Every recipient-accepting tool calls _check_recipients first
# --------------------------------------------------------------------------- #


def test_every_recipient_tool_checks_recipients_first() -> None:
    for name in ot.RECIPIENT_TOOL_NAMES:
        handler = ot.OUTLOOK_TOOL_HANDLERS[name]
        with patch.object(ot, "_check_recipients", return_value=json.dumps({"error": "blocked"})) as checked:
            result = _run(handler({"to": ["attacker@evil.example"], "subject": "x", "body": "x",
                                    "message_id": "m1"}))
        assert checked.called, f"{name} did not call _check_recipients"
        assert result == {"error": "blocked"}


def test_recipient_tool_names_matches_registered_tools() -> None:
    assert {t["name"] for t in ot.OUTLOOK_TOOLS} == ot.RECIPIENT_TOOL_NAMES


def test_outlook_tools_are_registered_with_the_executive() -> None:
    """Static registration (module-level list, no per-request branching) is
    what keeps the cached system-prompt tool list sorted and stable — see
    CLAUDE.md's prompt-caching invariant."""
    from openexecutive.orchestrator.executive import _ALL_SKILL_HANDLERS, _ALL_SKILL_TOOLS

    names = {t["name"] for t in _ALL_SKILL_TOOLS}
    assert ot.RECIPIENT_TOOL_NAMES <= names
    for name in ot.RECIPIENT_TOOL_NAMES:
        assert _ALL_SKILL_HANDLERS[name] is ot.OUTLOOK_TOOL_HANDLERS[name]


# --------------------------------------------------------------------------- #
# Roster gate
# --------------------------------------------------------------------------- #


def test_unknown_recipient_is_refused_and_audited() -> None:
    with patch("openexecutive.orchestrator.outlook_tools._exec_client") as client_factory:
        result = _run(ot.handle_send_outlook_email(
            {"to": ["attacker@evil.example"], "subject": "hi", "body": "body"}
        ))
    assert "error" in result
    assert "attacker@evil.example" in result["error"]
    client_factory.assert_not_called()


def test_roster_recipient_reaches_graph() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    fake_client = AsyncMock()
    with patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_send_outlook_email(
            {"to": ["alice@example.com"], "subject": "hi", "body": "body"}
        ))
    assert result.get("sent") is True
    fake_client.send.assert_awaited_once()


def test_exec_own_address_is_always_allowed() -> None:
    fake_client = AsyncMock()
    with patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_send_outlook_email(
            {"to": [EXEC_ADDR], "subject": "hi", "body": "body"}
        ))
    assert result.get("sent") is True


def test_disallowed_cc_blocks_a_send_with_an_allowed_to() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    with patch("openexecutive.orchestrator.outlook_tools._exec_client") as client_factory:
        result = _run(ot.handle_send_outlook_email(
            {"to": ["alice@example.com"], "cc": ["attacker@evil.example"], "subject": "hi", "body": "body"}
        ))
    assert "attacker@evil.example" in result["error"]
    client_factory.assert_not_called()


def test_reply_requires_message_id() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    result = _run(ot.handle_reply_outlook_email(
        {"to": ["alice@example.com"], "body": "body"}
    ))
    assert "message_id" in result["error"]


def test_reply_threads_and_reaches_graph() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    fake_client = AsyncMock()
    with patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_reply_outlook_email(
            {"message_id": "m1", "to": ["alice@example.com"], "body": "yes"}
        ))
    assert result.get("sent") is True
    sent_spec = fake_client.send.await_args.args[0]
    assert sent_spec.thread_id == "m1"


def test_draft_never_calls_send() -> None:
    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    fake_client = AsyncMock()
    fake_client.create_draft.return_value = CreatedDraft(draft_id="d1", message_id="d1", thread_id="")
    with patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_draft_outlook_email(
            {"to": ["alice@example.com"], "subject": "hi", "body": "body"}
        ))
    assert result == {"draft_id": "d1", "thread_id": ""}
    fake_client.create_draft.assert_awaited_once()
    fake_client.send.assert_not_called()


# --------------------------------------------------------------------------- #
# Contact egress — outlook_tools reuses mcp_gateway._roster_allow_set, which
# already gates contacts on a verified-surface/private turn. A contact absent
# from that set is refused exactly like a stranger's address.
# --------------------------------------------------------------------------- #


def test_a_contact_not_yet_reachable_is_refused_like_a_stranger() -> None:
    with patch(
        "openexecutive.orchestrator.mcp_gateway._roster_allow_set",
        return_value={EXEC_ADDR.lower()},
    ):
        result = _run(ot.handle_send_outlook_email(
            {"to": ["contact@partner.example"], "subject": "hi", "body": "body"}
        ))
    assert "contact@partner.example" in result["error"]


def test_a_contact_reachable_on_this_turn_is_allowed() -> None:
    fake_client = AsyncMock()
    with patch(
        "openexecutive.orchestrator.mcp_gateway._roster_allow_set",
        return_value={EXEC_ADDR.lower(), "contact@partner.example"},
    ), patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_send_outlook_email(
            {"to": ["contact@partner.example"], "subject": "hi", "body": "body"}
        ))
    assert result.get("sent") is True


# --------------------------------------------------------------------------- #
# Unconfigured Outlook
# --------------------------------------------------------------------------- #


def test_unconfigured_outlook_returns_a_plain_error() -> None:
    from openexecutive.integrations.outlook_client import GraphNotConfigured

    people_store.upsert_person(full_name="Alice", email="alice@example.com")
    fake_client = AsyncMock()
    fake_client.send.side_effect = GraphNotConfigured(EXEC_ADDR)
    with patch("openexecutive.orchestrator.outlook_tools._exec_client", return_value=fake_client):
        result = _run(ot.handle_send_outlook_email(
            {"to": ["alice@example.com"], "subject": "hi", "body": "body"}
        ))
    assert "not configured" in result["error"].lower()
