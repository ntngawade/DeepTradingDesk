# Oracle Cloud VM deployment

This deploys the existing Python app on an Ubuntu VM, with Caddy HTTPS and password protection. The Python backend listens only on loopback; Caddy is the only public web entry point. The default live-execution state remains paused after a new cloud deployment.

## Before creating the VM

- Create an Oracle Cloud account and an Ubuntu Always Free VM. Free capacity is not guaranteed in every region, and Oracle may reclaim idle Always Free compute.
- Reserve a **reserved public IPv4 address** and attach it to the VM. Do not later change the address used for Dhan orders: Dhan binds order access to the configured static IP, and changing it can be restricted by Dhan.
- Have a domain name whose DNS A record can point to that public IP. Caddy needs a public hostname to obtain and renew HTTPS certificates. Restrict the VM network security list/firewall to SSH (22) from your own IP, and HTTP/HTTPS (80/443); never expose port 8080.
- Do not put Dhan client IDs, tokens, passwords, or encryption keys in Git or send them in chat.

## Prepare the VM

SSH into the Ubuntu VM as its default administrator and run the following commands. Replace `YOUR_GITHUB_REPOSITORY` with the clone URL for your private or public repository. If private, configure GitHub SSH access on the VM first.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git debian-keyring debian-archive-keyring apt-transport-https curl gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
sudo useradd --system --home /opt/deeptradingdesk --shell /usr/sbin/nologin deepdesk
sudo install -d -o deepdesk -g deepdesk /opt/deeptradingdesk
sudo -u deepdesk git clone YOUR_GITHUB_REPOSITORY /opt/deeptradingdesk
sudo -u deepdesk python3 -m venv /opt/deeptradingdesk/venv
sudo -u deepdesk /opt/deeptradingdesk/venv/bin/pip install -r /opt/deeptradingdesk/requirements-cloud.txt
sudo install -d -o deepdesk -g deepdesk -m 700 /opt/deeptradingdesk/runtime
```

If GitHub is private, use a deploy key with read-only access. Never embed a GitHub token in the clone URL or service files.

## Configure the app secrets

Create a strong random proxy secret and a separate 32-byte encryption key on the VM. The following prints the values only in your SSH terminal; keep them private and store them in the root-owned environment file, not in the repository:

```bash
openssl rand -hex 32
openssl rand -base64 32
```

Create `/etc/deeptradingdesk.env` with mode 600, owned by root. Set the values below, substituting your own values; `DESK_ALLOWED_HOSTS` must exactly match the domain in the browser, without `https://` or a path.

```ini
DESK_BIND_HOST=127.0.0.1
DESK_ALLOWED_HOSTS=trades.example.com
DESK_PROXY_TOKEN=REPLACE_WITH_FIRST_RANDOM_VALUE
DESK_CREDENTIAL_KEY=REPLACE_WITH_SECOND_RANDOM_VALUE
```

The first random value must be the 64-character hex string. For `DESK_CREDENTIAL_KEY`, use the base64 output (normally 44 characters including `=`). Lock down the file:

```bash
sudo chown root:root /etc/deeptradingdesk.env
sudo chmod 600 /etc/deeptradingdesk.env
```

Cloud credential encryption uses AES-GCM and the key is external to the application files. Back up this key securely: losing it means saved Dhan credentials must be entered again. Dhan access tokens expire daily; enter a fresh token in Settings after deployment.

## Start the app behind HTTPS

Install the service and Caddy configuration from the repository:

```bash
sudo install -m 644 /opt/deeptradingdesk/deploy/oracle/deeptradingdesk.service /etc/systemd/system/deeptradingdesk.service
sudo install -m 644 /opt/deeptradingdesk/deploy/oracle/Caddyfile /etc/caddy/Caddyfile
```

Generate a Caddy password hash (`caddy hash-password`) and create `/etc/caddy/deeptradingdesk.env` containing:

```ini
DESK_DOMAIN=trades.example.com
DESK_WEB_USER=your-login-name
DESK_WEB_PASSWORD_HASH=PASTE_CADDY_HASH_HERE
DESK_PROXY_TOKEN=THE_SAME_FIRST_RANDOM_VALUE
```

Set this file root-owned and mode 600. Add a systemd override so Caddy receives it:

```bash
sudo systemctl edit caddy
```

Enter:

```ini
[Service]
EnvironmentFile=/etc/caddy/deeptradingdesk.env
```

Then start both services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now deeptradingdesk
sudo systemctl restart caddy
sudo systemctl status deeptradingdesk --no-pager
sudo journalctl -u deeptradingdesk -n 50 --no-pager
```

Before restarting Caddy, secure its environment file:

```bash
sudo chown root:root /etc/caddy/deeptradingdesk.env
sudo chmod 600 /etc/caddy/deeptradingdesk.env
```

Visit `https://trades.example.com` and sign in with the Caddy username/password. Verify that an unknown hostname is rejected and that `http://VM-IP:8080` is unreachable from outside.

## Move your existing app data safely

Do not clone or upload `runtime/` wholesale. The Windows `dhan-session.bin` uses Windows DPAPI and cannot be decrypted on Linux. Do not run both local and cloud live workers against the same enabled plans.

1. Pause new live submissions in the local app. Check Dhan for pending orders, open positions, and active CNC Super Order exits. Existing broker-side exits remain at Dhan; reconcile them before moving.
2. Export all plans/holdings from the app and save the tracker backup. Plans are stored in browser localStorage, so the cloud hostname starts with a separate browser store. Import the exported data after sign-in; check quantities and statuses before enabling anything.
3. If you need the prior idempotency/reconciliation history, stop the local server and take a consistent backup of `runtime/live.sqlite` (and `runtime/holding-exits.sqlite` if present). Copy only those databases to the cloud `runtime/`, set owner `deepdesk`, and retain an offline backup. Do not copy credentials or log files.
4. Enter and verify the daily Dhan token in the cloud app. Confirm the VM's reserved public IP is the one allowlisted in Dhan. Start with live execution paused, reconcile current Dhan positions/orders, then explicitly enable only the plans you intend to submit.

If you cannot pause the local worker or verify open broker orders, do not run the cloud worker yet. Running two workers can create conflicting submissions even though each ledger blocks its own duplicates.

## Known limitations

- This is a self-managed VM, not a managed service: you maintain OS updates, backups, DNS, and account security. Always Free availability and non-reclamation are not guaranteed.
- Chart Lab currently uses Windows OCR; that reader will report unavailable on Linux until ported to a Linux OCR engine. The rest of the app can run.
- Plans are browser-local rather than centrally synchronized. Import/export is required per browser/device. The SQLite live ledger is server-side and must be backed up.
- No public deployment or Dhan IP change is performed by these files. The owner must verify the VM, domain, HTTPS, Dhan token, IP allowlist, account state, and live-pause status before use.
