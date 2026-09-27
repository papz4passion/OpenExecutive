"""The Outlook poller starts with the FastAPI app only when Microsoft OAuth
is fully configured — mirrors test_slack_lifespan.py's approach: drive the
real `create_app()` lifespan through `TestClient`, faking the poller loop so
no network/credentials are involved."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from openexecutive.api.main import create_app


def _settings_with_outlook(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    from openexecutive.config import get_settings

    stub = get_settings().model_copy(update={
        "microsoft_oauth_client_id": overrides.get("microsoft_oauth_client_id", "cid"),
        "microsoft_oauth_client_secret": overrides.get("microsoft_oauth_client_secret", "sec"),
        "microsoft_oauth_tenant_id": overrides.get("microsoft_oauth_tenant_id", "common"),
        "exec_outlook_credentials_path": overrides.get(
            "exec_outlook_credentials_path", "/tmp/exec_outlook.json"
        ),
    })
    monkeypatch.setattr("openexecutive.config.get_settings", lambda: stub)


def test_lifespan_starts_outlook_poller_when_fully_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings_with_outlook(monkeypatch)

    async def _hang(*_args: Any, **_kwargs: Any) -> None:
        import asyncio
        await asyncio.Event().wait()

    with patch(
        "openexecutive.integrations.outlook_poller.run_outlook_poller",
        AsyncMock(side_effect=_hang),
    ) as run_poller:
        with TestClient(create_app()) as client:
            assert client.get("/health").status_code == 200
        run_poller.assert_called_once()


@pytest.mark.parametrize(
    "missing",
    ["microsoft_oauth_client_id", "microsoft_oauth_client_secret",
     "microsoft_oauth_tenant_id", "exec_outlook_credentials_path"],
)
def test_lifespan_skips_outlook_poller_without_full_config(
    monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    _settings_with_outlook(monkeypatch, **{missing: None})

    with patch(
        "openexecutive.integrations.outlook_poller.run_outlook_poller",
        AsyncMock(),
    ) as run_poller:
        with TestClient(create_app()) as client:
            assert client.get("/health").status_code == 200
        run_poller.assert_not_called()
