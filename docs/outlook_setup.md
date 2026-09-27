# Outlook Integration Setup

Open Executive can read and send mail through Microsoft 365 / Outlook as an
alternative to Gmail — both the Executive's own mailbox (inbound polling +
outbound send/reply/draft, roster-gated) and "Act as me" (read + draft only
for a teammate's own mailbox). It talks to Microsoft Graph directly; no MCP
server or extra process is involved. Mail only — no Calendar/OneDrive
equivalent yet.

---

## Step 1 — Register an Azure AD app

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active
   Directory → App registrations → New registration**.
2. Name it (e.g. `open-executive-outlook`).
3. **Supported account types**: choose **Accounts in any organizational
   directory (Any Microsoft Entra ID tenant - Multitenant)** unless you know
   you need single-tenant. Multitenant is the simpler default for a first
   deployment and works fine for a single organization too — you just never
   have to revisit it if you later add a second tenant. If your org's
   security policy requires locking the app to one tenant, choose
   **Accounts in this organizational directory only** instead and use that
   tenant's GUID for `MICROSOFT_OAUTH_TENANT_ID` (see Step 3).
4. Redirect URI: **Web**, pointing at wherever `scripts/connect-own-outlook.py`
   and the Executive's own-mailbox connect flow expect the OAuth callback
   (for local development this is a `localhost` URI printed by the script).
5. Click **Register**. Note the **Application (client) ID** — this becomes
   `MICROSOFT_OAUTH_CLIENT_ID`.

---

## Step 2 — Create a client secret

1. In the app registration, go to **Certificates & secrets → Client
   secrets → New client secret**.
2. Set an expiry per your org's policy and click **Add**.
3. Copy the secret **value** immediately (it is not shown again) — this
   becomes `MICROSOFT_OAUTH_CLIENT_SECRET`.

---

## Step 3 — Grant API permissions (delegated, Mail scopes)

1. Go to **API permissions → Add a permission → Microsoft Graph →
   Delegated permissions**.
2. Add:
   - `Mail.ReadWrite` — read and draft mail (both the Executive's own
     mailbox and a delegated "Act as me" mailbox use this; Graph has no
     separate compose-only scope).
   - `Mail.Send` — send/reply mail from the Executive's **own** mailbox
     only. A delegated mailbox never requests this scope, so there is no
     token capable of sending on a teammate's behalf.
   - `offline_access` — refresh tokens, so the connection survives past
     the initial sign-in (mirrors Gmail's refresh-token flow).
3. If your tenant requires admin consent for these scopes, click **Grant
   admin consent** (an org admin may need to do this).

---

## Step 4 — Set environment variables

```bash
MICROSOFT_OAUTH_CLIENT_ID=<Application (client) ID from Step 1>
MICROSOFT_OAUTH_CLIENT_SECRET=<client secret value from Step 2>
MICROSOFT_OAUTH_TENANT_ID=common   # or your tenant GUID if single-tenant
```

`common` works for both multitenant and single-tenant app registrations
signing in a single organization's users; use the tenant GUID only if you
chose "this organizational directory only" in Step 1.

All three are required together — Outlook stays fully disabled (no poller
task, no tools registered, "Act as me" reports "not configured") unless all
three are set, exactly like every other optional integration in this repo.

---

## Step 5 — Connect the Executive's own mailbox

Run the connect flow, signed in as `EXEC_EMAIL_ADDRESS`:

```bash
uv run python scripts/connect-own-outlook.py --email you@example.com
```

This writes a credential file and prints where to point
`EXEC_OUTLOOK_CREDENTIALS_PATH`:

```bash
EXEC_OUTLOOK_CREDENTIALS_PATH=./company/exec_outlook_credentials.json
```

(Docker: mount it under `/data`, same as the Gmail credentials.)

---

## Step 6 — "Act as me" (optional, per teammate)

Each teammate who wants Outlook drafts written in their voice runs the same
script signed in as themselves:

```bash
uv run python scripts/connect-own-outlook.py --email teammate@example.com
```

The credential lands in `DELEGATION_OUTLOOK_CREDENTIALS_DIR` (default
`./company/delegation_outlook`), one file per person, keyed by an address
hash — never by the plain address, and never in the Executive's own
credentials path from Step 5.

```bash
DELEGATION_OUTLOOK_CREDENTIALS_DIR=./company/delegation_outlook
```

A person can connect Gmail or Outlook for "Act as me", not both at once
(if both credential files exist, Gmail wins and a warning is logged). This
mailbox is read + draft only: there is no code path that can send from a
delegated mailbox, matching Gmail's Phase 1 restriction.

---

## Step 7 — Start the server and verify

```bash
make dev
```

Check connection status in **Settings** — the Outlook row (own-inbox and
Act as me) reports `connected`, `not configured`, `needs reconnect`, or
`error` alongside the existing Gmail row.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Outlook rows show "not configured" | One of the three `MICROSOFT_OAUTH_*` vars is missing | Set all three and restart |
| `needs reconnect` | Refresh token expired or was revoked | Re-run `connect-own-outlook.py` for that mailbox |
| `shared mailbox` status for a teammate | Their People-entry email equals the Executive's own Outlook address | Expected — Act as me refuses the Executive's own mailbox, same as Gmail |
| `mismatch` status | The credential's mailbox address no longer matches the person's current People-entry email | Re-run `connect-own-outlook.py` with the current address |
| Outbound send refused with an audit row | Recipient is not on the People roster and not the Executive's own address | Add the recipient to People, or address is intentionally blocked |
