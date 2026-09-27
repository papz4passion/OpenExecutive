"""The direct Outlook client for a person's own mailbox (delegation/outlook.py)
— mirrors test_delegation_gmail.py's status/no-send coverage."""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import stat
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openexecutive.delegation.outlook import (
    DelegateOutlook,
    outlook_for,
    outlook_status,
)
from openexecutive.integrations import outlook_client as oc
from openexecutive.integrations.outlook_client import (
    GraphAuthError,
    GraphCredential,
    GraphNotConfigured,
    OutlookClient,
    _GraphMailBase,
    load_credential,
)

EMAIL = "olivia@co.example"
EXEC = "ceo.test@example.com"  # tests/conftest.py's EXEC_EMAIL_ADDRESS
SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "connect-own-outlook.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("connect_own_outlook", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_delegate_outlook_has_no_send_method_anywhere_in_its_mro() -> None:
    """Drafts only: no method sends, and DelegateOutlook never inherits from
    OutlookClient (which legitimately has send() for the Executive's own
    mailbox)."""
    assert not issubclass(DelegateOutlook, OutlookClient)
    public = {n for n, _ in inspect.getmembers(DelegateOutlook, inspect.isfunction) if not n.startswith("_")}
    assert public == {
        "profile_email", "search_threads", "get_thread", "list_sent", "send_as_signature", "create_draft",
    }
    assert not hasattr(DelegateOutlook, "send")


def test_delegate_outlook_subclasses_the_shared_base() -> None:
    assert issubclass(DelegateOutlook, _GraphMailBase)


def test_outlook_for_returns_a_delegate() -> None:
    client = outlook_for(EMAIL)
    assert isinstance(client, DelegateOutlook)
    assert client.email == EMAIL


class _Mailbox:
    def __init__(self, opened: str = EMAIL, error: Exception | None = None) -> None:
        self.opened = opened
        self.error = error

    async def profile_email(self) -> str:
        if self.error is not None:
            raise self.error
        return self.opened


@pytest.mark.parametrize(("person_email", "mailbox", "expected"), [
    (EMAIL, _Mailbox(), "connected"),
    (EMAIL, _Mailbox(opened="other@co.example"), "mismatch"),
    (EMAIL, _Mailbox(error=GraphAuthError("invalid_grant")), "needs_reconnect"),
    (EMAIL, _Mailbox(error=GraphNotConfigured(EMAIL)), "not_configured"),
    (EMAIL, _Mailbox(error=RuntimeError("network")), "error"),
    ("", _Mailbox(), "no_email"),
    (EXEC.upper(), _Mailbox(opened=EXEC), "shared_mailbox"),
])
def test_status(person_email: str, mailbox: _Mailbox, expected: str) -> None:
    assert asyncio.run(outlook_status(person_email, outlook=mailbox)) == expected


def test_a_shared_mailbox_is_refused_before_microsoft_is_asked() -> None:
    mailbox = SimpleNamespace(profile_email=None)  # would fail if called
    assert asyncio.run(outlook_status(EXEC, outlook=mailbox)) == "shared_mailbox"


# --------------------------------------------------------------------------- #
# scripts/connect-own-outlook.py
# --------------------------------------------------------------------------- #


def test_the_script_writes_what_the_client_reads(tmp_path: Path) -> None:
    script = _script()
    assert script.email_key(" Olivia@Co.Example ") == oc.email_key(EMAIL)
    payload = script.credential_payload(EMAIL, "r1", "cid", "sec", "contoso-tenant")
    path = script.write_credential(tmp_path / "creds", EMAIL, payload)
    assert path.name == f"{oc.email_key(EMAIL)}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_credential(EMAIL, directory=tmp_path / "creds") == GraphCredential(
        email=EMAIL, refresh_token="r1", client_id="cid", client_secret="sec", tenant_id="contoso-tenant",
    )
    # The script asks for exactly the scopes the delegated client needs.
    assert list(script.SCOPES) == list(oc.DELEGATED_SCOPES)
