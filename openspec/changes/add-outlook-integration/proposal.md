# Proposal

## Why

Open Executive currently reads, drafts, and sends mail only through Gmail (the Executive's own inbox via the Google Workspace MCP, and a teammate's own mailbox via "Act as me"). Any user or company whose organization runs Microsoft 365 / Outlook instead of Google Workspace cannot use email at all today — inbound triage, auto-reply, and Act as me ghostwriting are all Gmail-only. Adding a parallel Outlook integration lets those users get the same executive-assistant behavior without switching mail providers.

## What Changes

- Add a direct Microsoft Graph API client for the Executive's **own** Outlook mailbox (`integrations/outlook_client.py`), analogous to `delegation/gmail.py`'s direct REST approach — no dependency on a third-party MCP server for Outlook, since none exists in this project the way `workspace-mcp` does for Gmail.
- Add an Outlook poller (`integrations/outlook_poller.py`) mirroring `integrations/email_poller.py`: polls unread mail, strips redirect-steering headers, filters automated/self-sent senders, classifies roster vs. non-roster vs. contact senders, hands the message to the Executive, and marks it read.
- Add native (non-MCP) orchestrator tools for the Executive's own Outlook mailbox — send, reply, and draft — gated the same way Gmail's are: every `to`/`cc`/`bcc` must resolve to a People-roster row or the exec's own address, enforced in the tool handler itself (there is no MCP gateway call to intercept, since Outlook isn't behind the MCP gateway).
- Add a delegated **Act as me** Outlook client (`delegation/outlook.py`) mirroring `delegation/gmail.py`: read-only + draft-only access to a teammate's own mailbox, drafts only (no send method exists, matching Gmail's Phase 1 restriction), a per-person OAuth credential file in a dedicated credentials directory, and a `outlook_status` check that verifies the token still opens the mailbox on the person's People-entry address and refuses the Executive's own address ("shared mailbox").
- Add a `scripts/connect-own-outlook.py` onboarding script (parity with `scripts/connect-own-gmail.py`) that runs the OAuth flow and writes the credential file.
- Extend the ghostwriting/voice-learner path so a person's Act as me provider (Gmail or Outlook) is selected by which mailbox they connected, rather than being hardcoded to Gmail; `ghostwrite_email` becomes provider-dispatching rather than Gmail-only.
- Extend Settings / connection-status UI and API to show Outlook connection state (own-inbox and Act as me) alongside the existing Gmail rows.
- New environment variables for Microsoft OAuth app registration (client id/secret, tenant) and an Outlook credentials directory, documented in `.env.example`.
- Update `architecture-facts.yaml` (`integrations`, `own_gmail`/new `own_outlook`) and the corresponding `prebuilt/integrations.json` (and any section naming Gmail as the only mail channel) per this repo's Architecture Docs rule.

No breaking changes: Gmail continues to work exactly as today: Outlook is additive and optional (unconfigured by default, like every other optional integration in this repo).

## Capabilities

### New Capabilities
- `integrations/outlook-inbox`: The Executive's own Outlook mailbox — polling unread mail into the Executive, and native send/reply/draft tools gated to the People roster, mirroring the Gmail inbound/outbound split (`channel_access` in architecture-facts.yaml).
- `delegation/outlook`: Act as me for a teammate's own Outlook mailbox — read-only + draft-only access via a per-person Microsoft OAuth credential, mirroring `delegation/gmail.py`'s Phase 1 restrictions (drafts only, mailbox-identity re-verified on every use, never reachable from the MCP gateway).

### Modified Capabilities
(none — no existing spec-level capability changes; Gmail's existing behavior is unchanged)

## Impact

- **New modules**: `packages/core/openexecutive/integrations/outlook_client.py`, `outlook_poller.py`; `packages/core/openexecutive/delegation/outlook.py`; `packages/core/openexecutive/orchestrator/outlook_tools.py` (native send/draft tools); `scripts/connect-own-outlook.py`.
- **Modified modules**: `config.py` (new Microsoft OAuth / credentials-dir settings), `delegation/ghostwriter.py` (provider dispatch), `api/main.py` / FastAPI lifespan (start the Outlook poller alongside the Gmail one, only when configured), Settings UI + its API routes (connection status rows), `.env.example`.
- **New dependency**: an HTTP client for Microsoft Graph (the project already depends on `httpx`, used directly — no new package expected, matching the direct-REST approach `delegation/gmail.py` takes).
- **Docs**: `architecture-facts.yaml` and `packages/core/openexecutive/architecture/prebuilt/integrations.json` (and any other prebuilt section naming Gmail as the sole mail integration).
- **Tests**: unit tests for the Graph client, poller (header-stripping/classification parity tests analogous to `test_email_poller` if it exists), delegation client (credential loading, drafts-only enforcement — Gmail has a unit test asserting no send method exists; Outlook needs the same), and route/integration tests for the roster-gated outbound tools.
- **Evals**: new eval scenarios are not required unless a specialist agent or prompt changes — this change adds an integration channel, not a new specialist, but a scenario covering "Outlook inbound triage" may be added under Evaluation criteria in design.md if the Executive's classification prompt needs Outlook-specific framing.
