"""The shape ``ghostwrite_email`` and the voice learner need from a person's
Act as me mailbox, satisfied structurally by both ``DelegateGmail``
(delegation/gmail.py) and ``DelegateOutlook`` (delegation/outlook.py) with no
changes to either — this is a ``Protocol``, not a base class.

Return types below are structural (``*Like``) so a concrete result class from
either provider (``gmail.CreatedDraft`` vs. ``outlook_client.CreatedDraft`` —
separate dataclasses with the same shape, not a shared base) satisfies them;
that direction works because a concrete class is always a structural subtype
of a Protocol with matching attributes. ``create_draft``'s ``spec`` parameter
is typed ``Any``: the reverse direction (a Protocol type assignable *into* a
concrete nominal parameter type like ``gmail.DraftSpec``) is not something
Python's structural typing supports, so pinning it to either provider's
concrete ``DraftSpec`` would make the other provider's client fail this
Protocol.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


@runtime_checkable
class ThreadSummaryLike(Protocol):
    id: str
    subject: str
    sender: str
    date: str


@runtime_checkable
class MailThreadLike(Protocol):
    id: str
    messages: list[Any]


@runtime_checkable
class CreatedDraftLike(Protocol):
    draft_id: str
    message_id: str
    thread_id: str


@runtime_checkable
class MailboxClient(Protocol):
    email: str

    async def profile_email(self) -> str: ...

    async def search_threads(self, query: str, *, max_results: int = 5) -> Sequence[ThreadSummaryLike]: ...

    async def get_thread(self, thread_id: str) -> MailThreadLike: ...

    async def list_sent(self, limit: int = 40) -> Sequence[Any]: ...

    async def send_as_signature(self) -> str: ...

    async def create_draft(self, spec: Any) -> CreatedDraftLike: ...


if TYPE_CHECKING:
    # mypy-only: DelegateGmail must satisfy MailboxClient with no changes to
    # gmail.py. Not evaluated at runtime, but checked on every `make lint`.
    from openexecutive.delegation.gmail import DelegateGmail

    def _gmail_satisfies_mailbox_client(client: DelegateGmail) -> MailboxClient:
        return client
