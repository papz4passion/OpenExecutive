# Deploy Open Executive on Google Cloud Platform

This runbook deploys Open Executive on one Google Compute Engine VM using
Docker Compose, a separate Persistent Disk, Caddy for automatic HTTPS, and
Google Sign-In for every user. Local passwordless login is disabled.

This topology matches current application constraints:

- API runs exactly one replica. Two API processes can fire scheduled actions
  twice.
- SQLite, ChromaDB, company documents, and OAuth credentials need a persistent
  local filesystem mounted at `/data`.
- Only UI origin is public. Browser traffic reaches API through UI server proxy.
- API receives outbound internet access but publishes no host port.

Cloud Run is not recommended for current architecture because API expects
durable filesystem state and must remain single-instance. Compute Engine keeps
storage and process model straightforward.

## Resulting topology

```text
Internet
   |
   | HTTPS :443
   v
Caddy container
   |
   v
Next.js UI container
   |
   | private Docker network + x-api-key
   v
FastAPI container (one replica)
   |
   v
Persistent Disk mounted at /srv/openexecutive-data
```

## Before starting

Required:

- Google Cloud project with billing enabled
- Domain or subdomain, such as `exec.example.com`
- Google Cloud CLI authenticated with permission to create Compute Engine,
  VPC, firewall, IP, and disk resources
- Anthropic API key, OpenRouter key, or configured local-model endpoint
- Google OAuth Web Application credentials for internet access
- Google accounts for every allowed user
- Operator public IP range for SSH, normally one `/32`

Published Open Executive images are `linux/amd64`. Use an x86-64 VM.

Expected baseline size:

- VM: `e2-standard-2` (2 vCPU, 8 GB RAM)
- Boot disk: 30 GB balanced Persistent Disk
- Data disk: 50 GB balanced Persistent Disk

API needs at least 2 GB RAM. Smaller whole-VM sizes leave little room for UI,
Caddy, Docker, model loading, and document ingestion.

## 1. Set deployment variables

Run from Cloud Shell or local shell with `gcloud` installed:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="asia-south1"
export ZONE="asia-south1-a"
export VM_NAME="openexecutive"
export DATA_DISK="openexecutive-data"
export ADDRESS_NAME="openexecutive-ip"
export NETWORK="openexecutive-vpc"
export SUBNET="openexecutive-subnet"
export APP_DOMAIN="exec.example.com"
export ADMIN_CIDR="203.0.113.10/32"

gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"
gcloud config set compute/zone "$ZONE"
```

Replace `ADMIN_CIDR` with operator's real public IPv4 address plus `/32`.
Using `0.0.0.0/0` for SSH exposes port 22 globally and is not recommended.

Enable required APIs:

```bash
gcloud services enable compute.googleapis.com dns.googleapis.com
```

Cloud DNS API is optional when DNS is managed elsewhere.

## 2. Create isolated network and firewall rules

Create custom VPC and subnet:

```bash
gcloud compute networks create "$NETWORK" \
  --subnet-mode=custom

gcloud compute networks subnets create "$SUBNET" \
  --network="$NETWORK" \
  --region="$REGION" \
  --range="10.20.0.0/24"
```

Allow public HTTP/HTTPS only for tagged VM:

```bash
gcloud compute firewall-rules create openexecutive-web \
  --network="$NETWORK" \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=openexecutive-web
```

Allow SSH only from operator address:

```bash
gcloud compute firewall-rules create openexecutive-ssh \
  --network="$NETWORK" \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:22 \
  --source-ranges="$ADMIN_CIDR" \
  --target-tags=openexecutive-ssh
```

Do not create firewall rules for ports 3000 or 8000.

## 3. Reserve static public IP

```bash
gcloud compute addresses create "$ADDRESS_NAME" \
  --region="$REGION" \
  --network-tier=PREMIUM

export STATIC_IP="$(gcloud compute addresses describe "$ADDRESS_NAME" \
  --region="$REGION" \
  --format='value(address)')"

echo "$STATIC_IP"
```

Keep static IP reserved and attached. Unattached reserved IPs can incur cost.

## 4. Create VM and persistent data disk

Create Ubuntu 24.04 LTS VM:

```bash
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --address="$STATIC_IP" \
  --network-tier=PREMIUM \
  --network="$NETWORK" \
  --subnet="$SUBNET" \
  --tags=openexecutive-web,openexecutive-ssh \
  --no-service-account \
  --no-scopes \
  --shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring
```

Create data disk without auto-delete coupling:

```bash
gcloud compute disks create "$DATA_DISK" \
  --zone="$ZONE" \
  --size=50GB \
  --type=pd-balanced

gcloud compute instances attach-disk "$VM_NAME" \
  --zone="$ZONE" \
  --disk="$DATA_DISK" \
  --device-name="$DATA_DISK" \
  --mode=rw
```

## 5. Create DNS record

At any DNS provider, create:

```text
Type: A
Name: exec
Value: STATIC_IP_FROM_STEP_3
TTL: 300
```

For Cloud DNS, when managed zone already exists:

```bash
export DNS_ZONE="your-managed-zone-name"

gcloud dns record-sets transaction start \
  --zone="$DNS_ZONE"

gcloud dns record-sets transaction add "$STATIC_IP" \
  --zone="$DNS_ZONE" \
  --name="${APP_DOMAIN}." \
  --ttl=300 \
  --type=A

gcloud dns record-sets transaction execute \
  --zone="$DNS_ZONE"
```

Wait until domain resolves:

```bash
dig +short "$APP_DOMAIN"
```

Output must match reserved static IP before Caddy can issue TLS certificate.

## 6. Configure mandatory Google Sign-In

This deployment uses Auth.js with Google OpenID Connect. It requests basic
identity scopes (`openid`, `email`, and `profile`) to authenticate users. This
is separate from optional Google Workspace MCP access to Gmail, Calendar, and
Drive.

In Google Cloud Console, under **Google Auth Platform**:

1. Open **Branding**:
   - Set app name, support email, and developer contact.
   - Add owned domain as authorized domain when prompted.
2. Open **Audience**:
   - Choose **Internal** for users in one Google Workspace organization.
   - Choose **External** when users can come from outside that organization.
   - While an External app remains in testing, add every intended account as a
     test user when console requires it.
3. Open **Clients** and create **OAuth Client ID → Web application**.
4. Add authorized JavaScript origin:

   ```text
   https://exec.example.com
   ```

5. Add authorized redirect URI:

   ```text
   https://exec.example.com/api/auth/callback/google
   ```

6. Save Client ID and Client secret securely. Secret is server-side and must
   never be placed in browser code or source control.

Use exact deployed origin. Scheme, hostname, port, callback path, and trailing
slash must match. Public Google OAuth redirects require HTTPS and should use
owned domain, not raw VM IP.

Open Executive applies second access gate after Google authenticates account:

- `ALLOWED_EMAILS` grants explicit operator access.
- Active People roster emails grant normal user access.
- Any account in neither list receives `AccessDenied` even after valid Google
  login.
- Removing user requires both archiving/removing roster entry and deleting
  email from `ALLOWED_EMAILS`.

## 7. Connect and format data disk

Connect:

```bash
gcloud compute ssh "$VM_NAME" --zone="$ZONE"
```

On VM, inspect disk:

```bash
DEVICE="/dev/disk/by-id/google-openexecutive-data"
ls -l "$DEVICE"
sudo blkid "$DEVICE" || true
```

> **Destructive first-use step:** Run `mkfs.ext4` only when `blkid` shows no
> existing filesystem and disk is newly created. Formatting an existing disk
> erases deployment data.

For new blank disk only:

```bash
sudo mkfs.ext4 -m 0 -F "$DEVICE"
```

Mount persistently:

```bash
sudo mkdir -p /srv/openexecutive-data
sudo mount "$DEVICE" /srv/openexecutive-data

DISK_UUID="$(sudo blkid -s UUID -o value "$DEVICE")"
echo "UUID=$DISK_UUID /srv/openexecutive-data ext4 defaults,nofail 0 2" \
  | sudo tee -a /etc/fstab

sudo mkdir -p \
  /srv/openexecutive-data/app \
  /srv/openexecutive-data/caddy-data \
  /srv/openexecutive-data/caddy-config

df -h /srv/openexecutive-data
```

Check `/etc/fstab` before appending if rerunning this step. Duplicate entries
should be removed.

## 8. Install Docker Engine and Compose

On VM, install from Docker's official Ubuntu repository:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt-get update
sudo apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

This guide keeps `sudo` on Docker commands. Adding user to `docker` group grants
root-equivalent access and is not required.

## 9. Clone repository

```bash
sudo mkdir -p /opt/openexecutive
sudo chown "$USER":"$USER" /opt/openexecutive

git clone https://github.com/SenteLabsAI/OpenExecutive.git \
  /opt/openexecutive

cd /opt/openexecutive
git checkout main
```

Production deployments should pin released image version, not `main` image.
Repository version at guide creation is `0.4.1`.

## 10. Create production environment file

Generate two different secrets:

```bash
openssl rand -base64 32
openssl rand -hex 32
```

Copy outputs somewhere secure. First becomes `AUTH_SECRET`; second becomes
`BACKEND_SHARED_SECRET`. Never put command substitutions such as `$(openssl …)`
inside `.env`; Compose treats them as literal strings.

Create protected file:

```bash
cd /opt/openexecutive
umask 077
install -m 600 /dev/null .env
nano .env
```

Minimum production configuration:

```dotenv
APP_DOMAIN=exec.example.com
CADDY_EMAIL=ops@example.com
OE_VERSION=0.4.1

ANTHROPIC_API_KEY=sk-ant-replace-with-real-key
DEFAULT_MODEL=claude-sonnet-5
DEEP_REASONING_MODEL=claude-opus-5
ROUTING_MODEL=claude-haiku-4-5

AUTH_SECRET=replace-with-generated-base64-value
AUTH_GOOGLE_ID=replace-with-google-client-id
AUTH_GOOGLE_SECRET=replace-with-google-client-secret
AUTH_TRUST_HOST=true
AUTH_URL=https://exec.example.com
ALLOWED_EMAILS=owner@example.com

BACKEND_SHARED_SECRET=replace-with-generated-hex-value
BACKEND_ALLOWED_ORIGINS=https://exec.example.com
OE_PUBLIC_DEPLOYMENT=1

ENABLE_WEB_SEARCH=false
EXEC_EMAIL_ADDRESS=owner@example.com
```

Important:

- `AUTH_URL` and `BACKEND_ALLOWED_ORIGINS` must use exact public HTTPS origin.
- `BACKEND_SHARED_SECRET` must be identical in UI and API. Compose below reads
  one value for both.
- `OE_PUBLIC_DEPLOYMENT=1` makes API fail closed if shared secret is absent.
- Put operator account in `ALLOWED_EMAILS` as break-glass access.
- Leave unused Slack, Discord, Telegram, Notion, Google Chat, and Workspace
  credentials unset. Do not copy sample tokens into production.
- To use OpenRouter or local models instead, follow
  [`../CONFIGURATION_GUIDE.md`](../CONFIGURATION_GUIDE.md).

## 11. Create production Compose file

Create `/opt/openexecutive/compose.gcp.yml`:

```yaml
name: openexecutive

services:
  api:
    image: ghcr.io/sentelabsai/openexecutive-api:${OE_VERSION}
    env_file:
      - .env
    environment:
      OE_LOCAL_LOGIN: ""
      VECTOR_STORE_PATH: /data/chroma_db
      COMPANY_PROFILE_PATH: /data/company/profile.yaml
      EPISODIC_DB_PATH: /data/episodic_memory.db
      MCP_SERVERS_CONFIG_PATH: /data/company/mcp_servers.json
      WORKSPACE_MCP_CREDENTIALS_DIR: /data/google_credentials
    volumes:
      - /srv/openexecutive-data/app:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 5m
    networks:
      - app

  ui:
    image: ghcr.io/sentelabsai/openexecutive-ui:${OE_VERSION}
    environment:
      NODE_ENV: production
      BACKEND_BASE_URL: http://api:8000
      AUTH_SECRET: ${AUTH_SECRET}
      AUTH_GOOGLE_ID: ${AUTH_GOOGLE_ID}
      AUTH_GOOGLE_SECRET: ${AUTH_GOOGLE_SECRET}
      AUTH_TRUST_HOST: ${AUTH_TRUST_HOST}
      AUTH_URL: ${AUTH_URL}
      ALLOWED_EMAILS: ${ALLOWED_EMAILS}
      BACKEND_SHARED_SECRET: ${BACKEND_SHARED_SECRET}
      OE_LOCAL_LOGIN: ""
      OE_PUBLIC_DEPLOYMENT: ${OE_PUBLIC_DEPLOYMENT}
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - app

  caddy:
    image: caddy:2-alpine
    environment:
      APP_DOMAIN: ${APP_DOMAIN}
      CADDY_EMAIL: ${CADDY_EMAIL}
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - /srv/openexecutive-data/caddy-data:/data
      - /srv/openexecutive-data/caddy-config:/config
    depends_on:
      - ui
    restart: unless-stopped
    networks:
      - app

networks:
  app:
    driver: bridge
```

No API or UI `ports` entry exists. Only Caddy publishes host ports.

If GHCR package is private, authenticate before pull with GitHub token carrying
`read:packages`:

```bash
echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
unset GHCR_TOKEN
```

## 12. Create Caddy configuration

Create `/opt/openexecutive/Caddyfile`:

```caddyfile
{
    email {$CADDY_EMAIL}
}

{$APP_DOMAIN} {
    encode zstd gzip
    reverse_proxy ui:3000
}
```

Caddy obtains and renews HTTPS certificate automatically after DNS points to VM
and ports 80/443 are reachable.

## 13. Validate and start

```bash
cd /opt/openexecutive

sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  config --quiet

sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  pull

sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  up -d

sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  ps
```

Cold API startup can take up to five minutes. Watch logs:

```bash
sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  logs -f api ui caddy
```

Stop log stream with `Ctrl+C`; containers keep running.

## 14. Verify deployment

From VM, verify API container directly:

```bash
sudo docker compose \
  --env-file .env \
  -f compose.gcp.yml \
  exec api curl -fsS http://localhost:8000/health
```

From local machine:

```bash
curl -fsS "https://$APP_DOMAIN/api/backend/health"
curl -I "https://$APP_DOMAIN"
```

Expected:

- Health JSON contains `"status":"ok"`.
- UI returns successful or auth redirect response.
- TLS certificate is valid for domain.
- Google login accepts only `ALLOWED_EMAILS` or active roster emails.
- Google account outside both lists receives `AccessDenied`.
- **Settings → Setup status** shows AI provider green.
- Test chat returns an answer.
- Ports 3000 and 8000 are not reachable from internet.

Complete company onboarding at:

```text
https://exec.example.com/onboard
```

## 15. Operations

Define helper alias on VM if desired:

```bash
cd /opt/openexecutive
COMPOSE="sudo docker compose --env-file .env -f compose.gcp.yml"
```

### Status and logs

```bash
$COMPOSE ps
$COMPOSE logs -f api
$COMPOSE logs -f ui
$COMPOSE logs -f caddy
```

### Restart

```bash
$COMPOSE restart api
$COMPOSE restart ui
$COMPOSE restart caddy
```

### Consistent SQLite backup

Create online SQLite backup before upgrades:

```bash
TS="$(date -u +%Y%m%dT%H%M%SZ)"
$COMPOSE exec -T api mkdir -p /data/backups
$COMPOSE exec -T api python -c \
  "import sqlite3; src=sqlite3.connect('/data/episodic_memory.db'); dst=sqlite3.connect('/data/backups/episodic_memory-${TS}.db'); src.backup(dst); dst.close(); src.close()"
ls -lh "/srv/openexecutive-data/app/backups/episodic_memory-${TS}.db"
```

Copy backups off VM or snapshot data disk. Live file copy of SQLite database can
be inconsistent; use SQLite backup API above.

Create disk snapshot from Cloud Shell/local shell:

```bash
gcloud compute snapshots create "openexecutive-data-$(date -u +%Y%m%d)" \
  --source-disk="$DATA_DISK" \
  --source-disk-zone="$ZONE"
```

Disk snapshots are crash-consistent. For full filesystem consistency, stop API
during snapshot window:

```bash
$COMPOSE stop api
```

Create snapshot from another authenticated shell, then:

```bash
$COMPOSE start api
```

### Upgrade

1. Back up database.
2. Edit `OE_VERSION` in `.env` to new release tag.
3. Pull images.
4. Stop API before replacement, preserving single-instance guarantee.
5. Recreate services.

```bash
cd /opt/openexecutive
$COMPOSE pull
$COMPOSE stop api ui
$COMPOSE up -d api
$COMPOSE up -d ui caddy
$COMPOSE ps
```

Re-run health and chat checks.

### Rollback

Set `OE_VERSION` back to previous release and repeat upgrade commands. Persistent
data is not rolled back with image. Restore disk snapshot or SQLite backup when
release introduced incompatible data migration.

### Rotate secrets

- Rotating `AUTH_SECRET` signs everyone out.
- Rotate `BACKEND_SHARED_SECRET` once, keeping one identical value for UI and
  API, then restart both.
- Rotate Google client secret in Google Cloud Console and `.env`, then restart
  UI.
- Rotate Anthropic key in provider console and `.env`, then restart API.

Never paste secrets into issue trackers, logs, shell history, or source control.

## 16. Monitoring

Minimum checks:

- HTTPS `GET /api/backend/health` every minute
- Disk usage for `/srv/openexecutive-data`
- Container restart counts
- API logs for authentication, scheduler, and provider errors
- TLS renewal failures in Caddy logs
- Snapshot/backup success

Commands:

```bash
df -h /srv/openexecutive-data
sudo docker stats --no-stream
$COMPOSE ps
$COMPOSE logs --since=1h api | tail -200
```

## 17. Troubleshooting

### Caddy cannot issue certificate

- Confirm `dig +short "$APP_DOMAIN"` equals VM static IP.
- Confirm firewall allows TCP 80 and 443.
- Confirm no proxy/CDN blocks ACME validation.
- Check `$COMPOSE logs caddy`.

### OAuth `redirect_uri_mismatch`

Authorized redirect URI must exactly equal:

```text
https://your-domain/api/auth/callback/google
```

Confirm `AUTH_URL` uses same origin and restart UI after changes.

### Google login ends on `AccessDenied`

Authentication succeeded, but email is not authorized by Open Executive. Add
email to `ALLOWED_EMAILS` and recreate UI, or add email to active People roster.
Also confirm Google reports account email as verified.

### Intended user cannot reach Google consent screen

Check Google Auth Platform **Audience**. For External app in testing, add user
as test user. For Internal app, account must belong to configured Workspace
organization.

### API refuses startup because shared secret is required

Expected fail-closed behavior. Set non-empty `BACKEND_SHARED_SECRET` while
keeping `OE_PUBLIC_DEPLOYMENT=1`.

### UI returns 401 for every backend call

UI and API received different shared secrets. Compose file uses same variable;
check `.env` formatting and recreate both containers.

### API stays unhealthy during first boot

Wait up to five minutes and inspect API logs. Ensure VM has at least 2 GB RAM,
data disk is mounted, `/srv/openexecutive-data/app` is writable, and provider
key is valid.

### Scheduled actions run twice

More than one API container/process exists. Run:

```bash
$COMPOSE ps
sudo docker ps --format '{{.Names}} {{.Image}}'
```

Stop duplicate API immediately. Deployment supports exactly one API replica.

### Data disappeared after restart

Confirm data disk mounted before Docker started:

```bash
findmnt /srv/openexecutive-data
ls -la /srv/openexecutive-data/app
```

If mount is missing, stop API before mounting disk. Starting against empty host
directory creates fresh database and can hide original data until correct disk
is mounted.

## 18. Teardown

Teardown is destructive. Snapshot data disk first if recovery might be needed.

Stop containers on VM:

```bash
cd /opt/openexecutive
sudo docker compose --env-file .env -f compose.gcp.yml down
```

From Cloud Shell/local shell:

```bash
gcloud compute instances delete "$VM_NAME" --zone="$ZONE"
gcloud compute disks delete "$DATA_DISK" --zone="$ZONE"
gcloud compute addresses delete "$ADDRESS_NAME" --region="$REGION"
gcloud compute firewall-rules delete openexecutive-web openexecutive-ssh
gcloud compute networks subnets delete "$SUBNET" --region="$REGION"
gcloud compute networks delete "$NETWORK"
```

Delete DNS A record and Google OAuth client separately. Retained snapshots and
DNS zones continue incurring cost.

## Official references

- [Create Linux VM](https://cloud.google.com/compute/docs/create-linux-vm-instance)
- [Reserve and assign static external IP](https://cloud.google.com/compute/docs/ip-addresses/configure-static-external-ip-address)
- [VPC firewall rules](https://cloud.google.com/firewall/docs/using-firewalls)
- [Create and attach Persistent Disk](https://cloud.google.com/compute/docs/disks/add-persistent-disk)
- [Persistent Disk snapshots](https://cloud.google.com/compute/docs/disks/create-snapshots)
- [Cloud DNS record sets](https://cloud.google.com/dns/docs/records)
- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Google OAuth for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Open Executive deployment notes](deployment.md)
- [Open Executive authentication notes](auth.md)
