# Open Executive Configuration Guide

Use this guide to configure Open Executive for local development, team access,
integrations, or production deployment. Keep every secret in the repo-root
`.env` file. Never commit `.env`, OAuth credentials, service-account files, or
tokens.

The exhaustive variable reference is [`.env.example`](.env.example). Detailed
security and hosting guidance lives in [`docs/auth.md`](docs/auth.md) and
[`docs/deployment.md`](docs/deployment.md).

## 1. Prerequisites

- Git
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 22.6 or newer with npm
- GNU Make through Git Bash or WSL on Windows, or run backend and UI separately
- An Anthropic, OpenRouter, or OpenAI-compatible local model provider

Docker is optional. First startup downloads Python packages, ChromaDB,
PyTorch, and an embedding model, so it can take several minutes.

## 2. Install dependencies

From repository root:

```bash
make install
```

Without Make:

```bash
cd packages/core
uv sync

cd ../ui
npm install
```

## 3. Create configuration file

From repository root:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Do not put real secrets in `.env.example`.

## 4. Choose one AI provider

At least one provider must be configured. Anthropic is simplest and matches
default models.

### Option A: Anthropic

Edit `.env`:

```dotenv
ANTHROPIC_API_KEY=sk-ant-your-real-key
DEFAULT_MODEL=claude-sonnet-5
DEEP_REASONING_MODEL=claude-opus-5
ROUTING_MODEL=claude-haiku-4-5
```

Set `ANTHROPIC_WORKSPACE_ID` only for an organization-scoped key that requires
workspace selection. Leave it blank for a workspace-scoped key.

### Option B: OpenRouter

```dotenv
ANTHROPIC_API_KEY=
OPENROUTER_ENABLED=true
OPENROUTER_API_KEY=your-openrouter-key
```

Choose models from Council settings after startup, or set model IDs directly:

```dotenv
DEFAULT_MODEL=provider/model-id
DEEP_REASONING_MODEL=provider/model-id
ROUTING_MODEL=provider/model-id
```

Optional catalog controls:

```dotenv
OPENROUTER_CATALOG_ENABLED=true
OPENROUTER_CATALOG_PROVIDERS=openai,google,anthropic,meta-llama,deepseek,x-ai
OPENROUTER_CATALOG_PER_PROVIDER=6
OPENROUTER_CATALOG_TIMEOUT_S=10
OPENROUTER_CATALOG_REFRESH_S=21600
```

### Option C: Local or self-hosted model

Start an OpenAI-compatible server such as Ollama, LM Studio, vLLM, or
llama.cpp, then configure:

```dotenv
ANTHROPIC_API_KEY=
LOCAL_MODELS_ENABLED=true
LOCAL_BASE_URL=http://localhost:11434/v1
LOCAL_API_KEY=
LOCAL_MODELS=llama3.3
LOCAL_TIMEOUT_S=300
DEFAULT_MODEL=llama3.3
DEEP_REASONING_MODEL=llama3.3
ROUTING_MODEL=llama3.3
```

Use `http://localhost:1234/v1` for LM Studio. `LOCAL_API_KEY` is optional for
Ollama and LM Studio. Model names must exactly match names served by local
provider.

Keep this disabled unless endpoint implements OpenRouter usage accounting:

```dotenv
LOCAL_INCLUDE_USAGE_ACCOUNTING=false
```

Native Anthropic web search is unavailable for local models.

## 5. Choose access mode

### Local-only mode

Leave Google OAuth fields blank:

```dotenv
AUTH_GOOGLE_ID=
AUTH_GOOGLE_SECRET=
AUTH_URL=
OE_PUBLIC_DEPLOYMENT=
BACKEND_SHARED_SECRET=
BACKEND_ALLOWED_ORIGINS=
```

`make dev` then:

- enables local login;
- binds UI to `127.0.0.1`;
- generates a temporary `AUTH_SECRET` when blank;
- treats local user as owner.

Do not expose this mode to LAN or internet.

### Team or public access

Create Google OAuth Web Application credentials. Add callback URIs:

```text
http://localhost:3000/api/auth/callback/google
https://your-host.example/api/auth/callback/google
```

Generate independent secrets:

```bash
openssl rand -base64 32
openssl rand -hex 32
```

Configure:

```dotenv
AUTH_SECRET=generated-base64-secret
AUTH_GOOGLE_ID=google-client-id
AUTH_GOOGLE_SECRET=google-client-secret
ALLOWED_EMAILS=owner@example.com,admin@example.com
AUTH_TRUST_HOST=true
AUTH_URL=https://your-host.example

BACKEND_SHARED_SECRET=generated-hex-secret
BACKEND_ALLOWED_ORIGINS=https://your-host.example
OE_PUBLIC_DEPLOYMENT=1
```

`BACKEND_SHARED_SECRET` must match in UI and API environments. Public API
refuses startup when `OE_PUBLIC_DEPLOYMENT=1` and shared secret is missing.

See [`docs/auth.md`](docs/auth.md) for allow-list behavior, secret rotation,
reverse proxies, and threat model.

## 6. Start locally

Git Bash, WSL, Linux, or macOS:

```bash
make dev
```

Open:

- UI: <http://localhost:3000>
- API health: <http://localhost:8000/health>
- MCP endpoint: <http://localhost:8000/mcp>

On Windows, Make recipes require Git Bash or WSL. If Make chooses wrong shell:

```bash
make dev SHELL="C:/Program Files/Git/bin/sh.exe"
```

Manual two-terminal startup:

Terminal 1:

```powershell
Set-Location packages/core
uv run uvicorn openexecutive.api.main:app --reload --port 8000
```

Terminal 2:

```powershell
Set-Location packages/ui
npm run dev -- -H 127.0.0.1
```

When starting manually, copy root configuration for Next.js:

```powershell
Copy-Item ..\..\.env .env.local -Force
```

For local login, also set `OE_LOCAL_LOGIN=1` and a temporary `AUTH_SECRET` in
both terminal environments. `make dev` handles these details automatically.

If Turbopack reports a Windows child-process `Access is denied` error, run UI
with webpack:

```powershell
npm run dev -- -H 127.0.0.1 --webpack
```

## 7. Complete in-app setup

Visit `/onboard` and provide:

1. Company name, industry, stage, and team size.
2. Business model and revenue context.
3. Competitive landscape.
4. Strategic priorities.
5. Culture and values.
6. Optional financial context and company documents.
7. Owner and team roster.
8. Department heads and approval responsibilities.

Then open **Settings → Setup status**. Resolve red checks before relying on
chat or scheduled work.

## 8. Optional integrations

Leave unused integration variables blank. Placeholder tokens such as
`xoxb-your-bot-token` trigger real connection attempts and noisy auth errors.

### Web search

```dotenv
ENABLE_WEB_SEARCH=false
WEB_SEARCH_MAX_USES=5
```

Enable only when live web lookup and its provider cost are wanted. Set only
one domain filter:

```dotenv
WEB_SEARCH_ALLOWED_DOMAINS=example.com,another.example
# WEB_SEARCH_BLOCKED_DOMAINS=untrusted.example
```

### Slack

```dotenv
SLACK_BOT_TOKEN=xoxb-real-token
SLACK_APP_TOKEN=xapp-real-token
```

Both tokens enable inbound Socket Mode. Bot token alone supports outbound
sends.

### Discord

```dotenv
DISCORD_BOT_TOKEN=real-token
DISCORD_APP_ID=application-id
DISCORD_GUILD_IDS=development-server-id
DISCORD_NOTIFY_CHANNEL_ID=optional-channel-id
DISCORD_THREAD_RESPONSE_GATE_ENABLED=true
```

Enable Discord **Message Content** intent and invite bot with `bot` plus
`applications.commands` scopes. Do not run standalone bot and API-embedded bot
at same time.

### Telegram

```dotenv
TELEGRAM_BOT_TOKEN=real-token
TELEGRAM_WEBHOOK_SECRET=generated-random-secret
```

Follow [`docs/telegram_setup.md`](docs/telegram_setup.md) for HTTPS webhook
registration and roster access.

### Google Chat

```dotenv
GOOGLE_CHAT_PROJECT_NUMBER=project-number
GOOGLE_CHAT_SERVICE_ACCOUNT_FILE=C:/secure/path/service-account.json
# Or use impersonation instead:
# GOOGLE_CHAT_SERVICE_ACCOUNT_EMAIL=service-account@example-project.iam.gserviceaccount.com
```

Use one service-account method, not both. Never store service-account JSON in
repository.

### Gmail, Calendar, and Drive through Google Workspace MCP

```dotenv
EXEC_EMAIL_ADDRESS=executive@example.com
GOOGLE_OAUTH_CLIENT_ID=desktop-oauth-client-id
GOOGLE_OAUTH_CLIENT_SECRET=desktop-oauth-client-secret
EMAIL_POLL_INTERVAL_SECONDS=60
```

Enable Gmail, Calendar, and Drive APIs, then mint credentials:

```bash
uv run --with mcp python scripts/mint-google-token.py
```

Credentials are stored under `.gworkspace-credentials/`, which is gitignored.
Copy `packages/core/mcp_servers.json.example` to configured company data path
when enabling MCP gateway tools.

### Notion wiki sync

```dotenv
NOTION_SYNC_ENABLED=true
NOTION_API_KEY=ntn_real-key
NOTION_SYNC_INTERVAL_MINUTES=60
NOTION_MAX_PAGES_PER_SCAN=40
```

Share only intended company wiki pages with Notion integration.

### Honcho memory

```dotenv
HONCHO_ENABLED=true
HONCHO_API_KEY=real-key
HONCHO_BASE_URL=
HONCHO_WORKSPACE_ID=openexec
HONCHO_PREFETCH_TIMEOUT_S=3.0
HONCHO_PREFETCH_MODE=representation
HONCHO_PREFETCH_MAX_CONCLUSIONS=20
```

Leave `HONCHO_BASE_URL` blank for hosted Honcho. Set it for self-hosted Honcho.

## 9. Data and persistence

Defaults are relative to repository root:

```dotenv
VECTOR_STORE_PATH=./chroma_db
COMPANY_PROFILE_PATH=./company/profile.yaml
EPISODIC_DB_PATH=./episodic_memory.db
```

Back up all three for persistent deployments. Docker stores state under
`/data`. API must run as one instance because scheduler is not safe across
multiple API replicas.

Workflow file access can be restricted:

```dotenv
WORKFLOW_FILE_DIRS=C:/approved/attachments,C:/approved/imports
```

## 10. Scheduler and automation controls

Useful defaults:

```dotenv
USER_TIMEZONE=Asia/Calcutta
PRINCIPAL_BRIEF_SUPPRESS_UNCHANGED=true
NUDGE_MAX_PER_SCOPE=3

ALERT_TTL_DAYS_MONITORING=3
ALERT_TTL_DAYS_ACTION=14
ALERT_REVIEW_ENABLED=true
ALERT_REVIEW_INTERVAL_HOURS=6
ALERT_REVIEW_MIN_AGE_HOURS=2
ALERT_REVIEW_MAX_PER_SCAN=25
ALERT_REVIEW_MAX_MOVES_PER_SCAN=10

OUTBOUND_MAX_PER_RECIPIENT_PER_WINDOW=5
OUTBOUND_RATE_WINDOW_MINUTES=60
OUTBOUND_DEDUP_WINDOW_MINUTES=360
OUTBOUND_RESPECT_QUIET_HOURS=true
```

Times set in app override `USER_TIMEZONE`. Explicit brief-time variables are
interpreted as UTC. Keep client rotation off unless automated nightly LLM cost
and client switching are intended.

## 11. Model, timeout, research, and RAG tuning

Start with defaults. Change only after observing real failures:

```dotenv
ENABLE_CACHING=true
CHAT_STREAM_TIMEOUT_S=300
COMMITTEE_EXTRA_TIMEOUT_S=60
INTERVIEW_TIMEOUT_S=120

WATCHLIST_RESEARCH_INTERVAL_MINUTES=360
WATCHLIST_RESEARCH_MAX_STALENESS_HOURS=168
RESEARCH_WEB_SEARCH_MAX_USES=3
WATCHLIST_RESEARCH_MAX_DIRECT_ADDS=2
WATCHLIST_RESEARCH_MAX_PROPOSALS=2
WATCHLIST_MAX_ENABLED=40
WATCHLIST_PROPOSAL_TTL_DAYS=14

KNOWLEDGE_DISTANCE_THRESHOLD=0.55
# KNOWLEDGE_BUILTIN_DISTANCE_THRESHOLD=0.50
```

Lower knowledge distance means stricter retrieval. Raise shared threshold when
relevant company documents consistently fail retrieval; lower built-in
threshold when generic handbook content dominates results.

## 12. Docker deployment

```bash
make docker
```

Equivalent:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

Stop:

```bash
docker compose --env-file .env -f docker/docker-compose.yml down
```

For any internet-reachable deployment, require all of:

```dotenv
AUTH_SECRET=strong-random-value
AUTH_GOOGLE_ID=google-client-id
AUTH_GOOGLE_SECRET=google-client-secret
AUTH_URL=https://your-host.example
ALLOWED_EMAILS=owner@example.com
BACKEND_SHARED_SECRET=independent-strong-random-value
BACKEND_ALLOWED_ORIGINS=https://your-host.example
OE_PUBLIC_DEPLOYMENT=1
```

Also configure persistent storage, TLS, health checks, backups, and one API
replica. See [`docs/deployment.md`](docs/deployment.md).

## 13. Verification checklist

Check backend:

```bash
curl http://localhost:8000/health
```

Expected: JSON containing `"status":"ok"`.

Check UI:

```bash
curl -I http://localhost:3000
```

Then verify:

- UI opens and login mode matches intended deployment.
- **Settings → Setup status** shows AI provider green.
- Test chat returns model response.
- Company profile and owner are configured.
- Optional integrations show green only when enabled.
- `.env` is ignored: `git status --short` must not list it.
- Public API rejects requests without shared secret.
- Public deployment uses HTTPS and exactly one API replica.

## 14. Common failures

### AI setup shows sample key

Replace this:

```dotenv
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

with real key, then restart backend.

### `-f was unexpected at this time` on Windows

Make used `cmd.exe` for POSIX recipe. Run from Git Bash or WSL, or set Make's
shell explicitly.

### Auth.js `MissingSecret`

Set `AUTH_SECRET`, or use `make dev`, which generates temporary local secret.

### Slack `invalid_auth` during startup

Clear sample Slack values unless integration is configured:

```dotenv
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=
```

### UI cannot reach API

Confirm API listens on port 8000. For deployed UI set backend base URL in its
runtime environment and ensure `BACKEND_SHARED_SECRET` matches API.

### Company documents are not indexed

Create configured company document directory or upload files through UI. Check
`VECTOR_STORE_PATH` is writable and persistent.

### Configuration change has no effect

Restart both API and UI after `.env` changes. If starting UI manually, refresh
`packages/ui/.env.local` from root `.env` first.
