# Design

## Context

See proposal.md - Why. Two existing Gmail tracks to mirror:

1. **Executive's own inbox** (`integrations/email_poller.py`): reached through the Google Workspace MCP server (`google_workspace__*` tools), gated at `orchestrator/mcp_gateway.py` (`_check_acting_account`, `_check_gmail_recipients`, `_GATED_GMAIL_TOOLS`). The model calls these tools directly; the gateway intercepts and refuses bad recipients before the call reaches the MCP server.
2. **Act as me** (`delegation/gmail.py`): a direct Gmail REST client (httpx), never registered with the MCP gateway, reachable only through fixed typed handlers (`ghostwrite_email`, the voice learner). Drafts only — no send method exists at all (enforced by a unit test).

The user has confirmed: build **both** tracks for Outlook, and reach Outlook via a **direct Microsoft Graph API client** (not an MCP server) for both tracks, since there is no existing "Outlook MCP" analogous to `workspace-mcp` in this project.

## Goals / Non-Goals

**Goals:**
- Executive's own Outlook mailbox: inbound polling + outbound send/reply/draft, with the same roster-gated safety Gmail has today.
- Act as me for Outlook: read + draft only, same safety posture as Gmail's Phase 1.
- Gmail continues to work completely unchanged; Outlook is purely additive and optional.
- Ghostwriting (`ghostwrite_email`) becomes provider-dispatching so a person can use either Gmail or Outlook for Act as me, keyed by which one they've connected.

**Non-Goals:**
- No Outlook Calendar or Drive-equivalent (SharePoint/OneDrive) integration — Gmail's Calendar/Drive gating is out of scope; this change is mail only.
- No send capability for delegated (Act as me) Outlook mailboxes — matches Gmail's current Phase 1 restriction, not a new limitation.
- No migration of existing Gmail users to Outlook, and no multi-provider mailbox per person (a person has at most one Act as me mailbox: whichever they connected).
- No change to Slack/Discord/Telegram/Google Chat channel behavior.

## Decisions

### 1. Direct Microsoft Graph API client, not an MCP server, for BOTH tracks
Gmail's own-inbox track goes through an MCP server because that server (`workspace-mcp`) already exists and is co-located in this deployment. No equivalent exists for Outlook, and standing one up is out of scope (a new external dependency to build, host, and maintain). A direct Graph client for the Executive's own inbox means:
- The Executive's own Outlook mailbox is **not** reachable through the generic MCP gateway/tool-calling surface the way Gmail is. Instead, native orchestrator tools (`orchestrator/outlook_tools.py`) wrap the Graph client directly, analogous to how `orchestrator/calendar_tools.py` or `orchestrator/schedule_tools.py` expose native (non-MCP) tools today.
- The roster gate that Gmail gets "for free" via `mcp_gateway._check_gmail_recipients` must be implemented as an explicit check *inside* each native Outlook tool handler (`send`, `reply`, `draft`) before the Graph API call is made. This is the same security property, just enforced at a different layer — call it out clearly in code comments so a future refactor doesn't assume the MCP gateway covers Outlook too.

Alternative considered: build a minimal internal Outlook MCP server and register it like `google_workspace`. Rejected — adds an extra process/deployment surface and a JSON-RPC layer for no behavioral benefit when a direct client is simpler and match the precedent already set by `delegation/gmail.py`.

### 2. Poller mirrors `email_poller.py`'s structure, not its Gmail-specific parsing
`email_poller.py`'s message-parsing helpers (`_split_gmail_content`, `_attachment_line`, etc.) exist because the Gmail MCP tool returns **plain text**, not structured JSON. Microsoft Graph's `GET /me/messages` returns structured JSON (headers, HTML/text body, attachments as separate fields) directly — no text-marker parsing is needed. `outlook_poller.py` will reuse the *behavioral* logic (redirect-header stripping is still needed since Graph exposes raw headers too; quote/signature stripping for `sender_new_text`/peer-memory purposes; forwarded-message detection) but reimplement it against structured fields rather than porting the marker-based text parser. The quote-stripping heuristics (`_new_text_lines`, attribution/forwarded detection) are provider-agnostic text logic and can be shared as-is against the plain-text body Graph returns.

### 3. `ghostwrite_email` becomes provider-dispatching
Today `delegation/gmail.py` is the only mailbox client `ghostwrite_email` (and the voice learner) can use. This change introduces `delegation/outlook.py` as a second, and the handler must pick the right one per person. Decision: add a small provider-resolution step (e.g. `delegation/mailbox.py::mailbox_for(person)` returning a `DelegateGmail` or `DelegateOutlook`, or `None` with a status) rather than duplicating `ghostwrite_email` per provider. Both clients expose the same shape (`profile_email`/`status`, `search_threads`/`get_thread`/`list_sent`, `create_draft`, `send_as_signature`) so the handler's logic stays provider-agnostic; only the concrete client differs. A person connects at most one provider — `can_delegate`/`is_enabled` stay per-person booleans, and which mailbox backs them is determined by which credential file exists (Gmail credential dir vs. Outlook credential dir), not a new stored "provider" column, to avoid a schema migration. If both happen to exist for one person (shouldn't normally happen since the connect script only offers one path at a time), Gmail wins for backward compatibility, and this is logged as a warning.

### 4. Separate credential directories and OAuth app registration
Outlook credentials live in their own directory (`DELEGATION_OUTLOOK_CREDENTIALS_DIR`, new env var) written by `scripts/connect-own-outlook.py`, keyed by the same `email_key()`-style hash so addresses never appear in filenames — mirrors `delegation/gmail.py`'s `credentials_dir()`/`credential_path()` exactly. Microsoft's OAuth app registration needs `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET`, and `MICROSOFT_OAUTH_TENANT_ID` (or `common` for multi-tenant) — new settings fields, all optional (absent ⇒ Outlook disabled everywhere), following the existing pattern of every optional integration in `config.py`.

### 5. Scopes
- Executive's own mailbox (send/draft/read): `Mail.ReadWrite`, `Mail.Send`.
- Delegated (Act as me) mailbox (read + draft only, no send): `Mail.Read`, `Mail.ReadWrite` is still needed to create a draft (Graph has no separate "compose-only" scope the way Gmail's `gmail.compose` is), but the delegation client code itself simply never calls a send endpoint — same enforcement style as Gmail's "no send method exists, a unit test asserts it."

### 6. Threading / reply headers
Graph messages carry `internetMessageId`, `conversationId`, and the raw `references`/`in-reply-to` headers are retrievable via `internetMessageHeaders` on a `$select`. Reuse the same `references_header()` logic from `delegation/gmail.py` (provider-agnostic string manipulation) against Graph's raw headers.

## Risks / Trade-offs

- **[Risk]** Enforcing the roster gate inside each native tool handler (rather than one central gateway chokepoint like Gmail's) means a future new Outlook tool could forget the check. → **Mitigation**: a single shared helper (`outlook_tools._check_recipients`) that every send/reply/draft tool must call first, plus a unit test enumerating all registered Outlook tools and asserting each one that accepts recipients calls the helper (analogous in spirit to the Gmail gateway's centralized gate, just structured as a required call instead of an interception point).
- **[Risk]** Microsoft Graph's delta/webhook subscription model differs from polling; polling Graph's `/me/mailFolders/inbox/messages?$filter=isRead eq false` is simpler and directly mirrors the existing Gmail poller's operational model, at the cost of the same latency/quota trade-offs Gmail already accepts. → **Mitigation**: none needed — this matches existing behavior, not a new risk.
- **[Risk]** Two mailbox client shapes (Gmail vs. Outlook) drifting out of sync over time since they're not literally the same interface (duck-typed, not a formal `Protocol`). → **Mitigation**: define a `Protocol` (`delegation/mailbox_protocol.py`) that both `DelegateGmail` and `DelegateOutlook` are checked against with `isinstance`/mypy structural typing, so `ghostwrite_email` can be written once against the protocol.
- **[Trade-off]** No Outlook MCP server means the Executive's own Outlook tools live outside the MCP gateway's existing gated-tool machinery (`_GATED_GMAIL_TOOLS`, `_GATED_CALENDAR_TOOLS`). This is intentional per the user's transport choice, but it does mean two different enforcement mechanisms exist for "the same" security property (roster-gated outbound mail) depending on provider. Documented explicitly in `architecture-facts.yaml` so it isn't mistaken for an oversight later.

## Migration Plan

- Purely additive: no existing data migrates. Rollout is config-gated (absence of Microsoft OAuth env vars ⇒ fully inert, no new poller task starts, no new tools registered).
- Rollback: unset the Microsoft OAuth env vars and remove any stored Outlook credential files; no schema changes to roll back (Outlook credential files are freestanding, like Gmail's, not a DB table).

## Open Questions

- Multi-tenant vs. single-tenant Azure AD app registration guidance for `docs/outlook_setup.md` (mirrors `docs/google_chat_setup.md` / `docs/telegram_setup.md`) — can be resolved during implementation without affecting the specs or task breakdown; write the doc against whichever tenant mode is simplest for a first-time operator (Microsoft's default "accounts in this organizational directory only" vs. "any organizational directory") and note the alternative.
