"""Provider resolution for Act as me (delegation/mailbox.py) — Gmail vs.
Outlook, decided by which credential file exists, Gmail winning a tie."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openexecutive.delegation import gmail as gm
from openexecutive.delegation import outlook as do
from openexecutive.delegation.mailbox import mailbox_for, mailbox_status
from openexecutive.integrations import outlook_client as oc

EMAIL = "olivia@co.example"


def _write_gmail_credential(directory: Path, email: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{gm.email_key(email)}.json").write_text(json.dumps({
        "version": 1, "email": email,
        "authorized_user": {"refresh_token": "r", "client_id": "c", "client_secret": "s"},
    }))


def _write_outlook_credential(directory: Path, email: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{oc.email_key(email)}.json").write_text(json.dumps({
        "version": 1, "email": email,
        "authorized_user": {
            "refresh_token": "r", "client_id": "c", "client_secret": "s", "tenant_id": "contoso",
        },
    }))


@pytest.fixture
def gmail_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "gmail"
    monkeypatch.setattr(gm, "credentials_dir", lambda: directory)
    return directory


@pytest.fixture
def outlook_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "outlook"
    monkeypatch.setattr(oc, "credentials_dir", lambda: directory)
    return directory


def test_gmail_only(gmail_dir: Path, outlook_dir: Path) -> None:
    _write_gmail_credential(gmail_dir, EMAIL)
    client = mailbox_for(EMAIL)
    assert isinstance(client, gm.DelegateGmail)


def test_outlook_only(gmail_dir: Path, outlook_dir: Path) -> None:
    _write_outlook_credential(outlook_dir, EMAIL)
    client = mailbox_for(EMAIL)
    assert isinstance(client, do.DelegateOutlook)


def test_both_present_gmail_wins_and_warns(
    gmail_dir: Path, outlook_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.delegation import mailbox as mb

    _write_gmail_credential(gmail_dir, EMAIL)
    _write_outlook_credential(outlook_dir, EMAIL)
    warnings: list[str] = []
    monkeypatch.setattr(mb.logger, "warning", lambda msg, *args, **kw: warnings.append(msg % args))
    client = mailbox_for(EMAIL)
    assert isinstance(client, gm.DelegateGmail)
    assert any("Gmail wins" in w for w in warnings)


def test_neither_present(gmail_dir: Path, outlook_dir: Path) -> None:
    assert mailbox_for(EMAIL) is None


def test_empty_email(gmail_dir: Path, outlook_dir: Path) -> None:
    assert mailbox_for("") is None


# --------------------------------------------------------------------------- #
# mailbox_status
# --------------------------------------------------------------------------- #


def test_status_not_configured_when_neither_connected(gmail_dir: Path, outlook_dir: Path) -> None:
    assert asyncio.run(mailbox_status(EMAIL)) == "not_configured"


def test_status_no_email() -> None:
    assert asyncio.run(mailbox_status("")) == "no_email"


def test_status_dispatches_to_outlook_when_the_client_is_a_delegate_outlook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch is by isinstance(client, DelegateOutlook) — not by re-deriving
    which credential exists a second time."""
    client = do.DelegateOutlook(EMAIL)
    monkeypatch.setattr(client, "profile_email", _async(EMAIL))
    assert asyncio.run(mailbox_status(EMAIL, mailbox=client)) == "connected"


def test_status_dispatches_to_gmail_when_the_client_is_a_delegate_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = gm.DelegateGmail(EMAIL)
    monkeypatch.setattr(client, "profile_email", _async(EMAIL))
    assert asyncio.run(mailbox_status(EMAIL, mailbox=client)) == "connected"


def _async(value: str) -> object:
    async def _inner() -> str:
        return value
    return _inner
