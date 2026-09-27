"""``ghostwrite_email`` provider-dispatching to Outlook (Act as me) — the
same flows test_delegation_tools.py covers for Gmail, run against an
Outlook-backed writer to confirm delegation_tools.py's dispatch logic."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openexecutive.audit import logger as audit_logger
from openexecutive.audit.logger import AuditLogger
from openexecutive.delegation import ghostwriter as gw
from openexecutive.delegation import outlook as do
from openexecutive.delegation.settings import DelegationOverride, pin_turn_delegation
from openexecutive.integrations.outlook_client import (
    CreatedDraft,
    DraftSpec,
    MailMessage,
    MailThread,
)
from openexecutive.memory import episodic
from openexecutive.orchestrator import delegation_tools as dt
from openexecutive.orchestrator.schedule_tools import current_session, set_session
from openexecutive.orchestrator.session import Session
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store
from openexecutive.people.models import Person

OWNER = "olivia@co.example"
DANA = "dana@northpeak.example"


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(people_store, "DB_PATH", path)
    episodic.initialize_db(path)
    people_store.initialize_db(path)
    people_registry.invalidate()
    monkeypatch.setattr(audit_logger, "_default_logger", AuditLogger(db_path=path))
    prior = current_session.get()
    current_session.set(None)
    yield path
    current_session.set(prior)
    people_registry.invalidate()


@pytest.fixture(autouse=True)
def fresh_turn_state() -> Iterator[None]:
    from openexecutive.delegation import settings as dsettings

    token = dsettings._TURN.set(None)
    dt._SAVED_TODAY.clear()
    dt._IN_FLIGHT.clear()
    yield
    dsettings._TURN.reset(token)
    dt._SAVED_TODAY.clear()
    dt._IN_FLIGHT.clear()


@pytest.fixture
def roster() -> SimpleNamespace:
    principal = people_store.upsert_person(full_name="Olivia Owner", is_principal=True, email=OWNER)
    people_registry.invalidate()
    return SimpleNamespace(principal=principal)


class FakeOutlookMailbox(do.DelegateOutlook):
    """A DelegateOutlook so delegation_tools._is_outlook(...) recognizes it,
    with FakeMailbox's (test_delegation_tools.py) duck-typed behavior."""

    def __init__(self, *, opened: str = OWNER) -> None:
        super().__init__(OWNER)
        self.opened = opened
        self.threads: dict[str, MailThread] = {
            "t1": MailThread(id="t1", messages=[
                MailMessage(
                    id="m1", thread_id="t1", from_addr=DANA, from_name="Dana",
                    to=[OWNER], subject="Brand refresh pilot", date="Mon 1",
                    message_id_header="<m1@mail.example>", text="Can we start the pilot Oct 5?",
                ),
            ]),
        }
        self.drafts: list[DraftSpec] = []

    async def profile_email(self) -> str:
        return self.opened

    async def get_thread(self, thread_id: str) -> MailThread:
        return self.threads[thread_id]

    async def create_draft(self, spec: DraftSpec) -> CreatedDraft:
        self.drafts.append(spec)
        return CreatedDraft(
            draft_id="d1", message_id="d1", thread_id="t1",
            web_link="https://outlook.office.com/mail/deeplink/read/d1",
        )


def _owner() -> Person:
    person = people_store.find_principal_person()
    assert person is not None
    return person


def _session(mailbox: FakeOutlookMailbox, speaker_text: str = "reply to Dana as me") -> Session:
    session = Session(delegation_override=DelegationOverride(enabled=True, gmail=mailbox, person=_owner()))
    assert pin_turn_delegation(session, speaker_text).offered
    return session


@pytest.fixture
def composer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    turns: list[str] = []

    async def fake(model: str, system: str, turn: str) -> dict[str, Any]:
        turns.append(turn)
        return {"subject": "Hello", "body": "Hi Dana,\n\nYes to Oct 5.\n\nBest,\nOlivia", "open_questions": []}

    monkeypatch.setattr(gw, "_call_model", fake)
    return turns


def _run(session: Session | None, tool_input: dict[str, Any]) -> dict[str, Any]:
    async def go() -> str:
        with set_session(session):
            return await dt.handle_ghostwrite_email(tool_input)

    return json.loads(asyncio.run(go()))


def test_a_reply_drafts_through_outlook_threaded_on_the_message_id(
    roster: SimpleNamespace, composer: list[str],
) -> None:
    """DraftSpec.thread_id must be the Graph MESSAGE id (m1), not the
    conversation id (t1) — Outlook's createReply needs a specific message."""
    mailbox = FakeOutlookMailbox()
    result = _run(_session(mailbox), {"intent": "Yes to Oct 5.", "thread_id": "t1"})
    assert result["status"] == "drafted"
    spec = mailbox.drafts[0]
    assert isinstance(spec, DraftSpec)
    assert spec.to == [DANA]
    assert spec.thread_id == "m1"
    assert result["gmail_link"] == "https://outlook.office.com/mail/deeplink/read/d1"
    assert "nothing was sent" in result["note"]


def test_a_new_email_drafts_through_outlook_with_no_thread(
    roster: SimpleNamespace, composer: list[str],
) -> None:
    mailbox = FakeOutlookMailbox()
    session = _session(mailbox, speaker_text=f"email {DANA} as me")
    result = _run(session, {"intent": "Hi.", "to": [DANA]})
    assert result["status"] == "drafted"
    spec = mailbox.drafts[0]
    assert isinstance(spec, DraftSpec)
    assert spec.thread_id is None


def test_a_mismatched_outlook_mailbox_is_refused(roster: SimpleNamespace) -> None:
    session = _session(FakeOutlookMailbox(opened="someone.else@co.example"))
    result = _run(session, {"intent": "x", "thread_id": "t1"})
    assert result["status"] == "mismatch"
    assert session.turn_delegation.touched_mail is True


def test_outlook_status_messages_are_used_not_gmails(roster: SimpleNamespace) -> None:
    session = _session(FakeOutlookMailbox(opened="someone.else@co.example"))
    result = _run(session, {"intent": "x", "thread_id": "t1"})
    assert do.STATUS_MESSAGES["mismatch"] in result["error"]
