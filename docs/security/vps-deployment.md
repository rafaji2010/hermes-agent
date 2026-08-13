# Deploying Hermes on a VPS (Hardened)

A concise operations guide for running the Hermes gateway on a single Linux
VPS. The default posture is **loopback-first**: bind every Hermes listener to
`127.0.0.1` and reach it over an encrypted tunnel. Nothing is exposed to the
public internet unless you deliberately front it with a TLS-terminating
reverse proxy.

> The command-approval floor (hardline blocklist) and the iron-proxy egress
> isolation are enforced in the Hermes core, not by this guide. What follows
> hardens the *transport and process* layer around them.

---

## 1. Loopback bind (the default)

The gateway dashboard proxy listens on `127.0.0.1:9119` and the agent core
binds loopback-only listeners. Verify your config does **not** widen this:

```bash
# config.yaml — the dashboard/API listener must stay loopback
# (or be omitted entirely; 127.0.0.1 is the default)
ss -tlnp | grep 9119   # expect 127.0.0.1:9119, never 0.0.0.0:9119
```

If anything reports `0.0.0.0`, stop and fix it before proceeding — a
publicly-reachable agent control plane is the failure this guide exists to
prevent.

---

## 2. Remote access: Tailscale or an SSH tunnel

You want to reach the loopback service from your own machine without exposing
it to the world. Pick one.

### Option A — Tailscale (`tailscale serve`, TLS included)

```bash
# Install and bring up the tailnet (one-time):
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Serve the loopback dashboard over Tailscale HTTPS:
tailscale serve --bg --https=443 http://127.0.0.1:9119
```

`tailscale serve` terminates TLS with a Tailscale-managed certificate and only
accepts traffic from your tailnet. No public DNS, no open firewall port.

### Option B — SSH local tunnel (no extra daemon)

```bash
# From your laptop — keep this running in a terminal:
ssh -N -L 9119:127.0.0.1:9119 hermes@YOUR_VPS_IP
# Then browse http://127.0.0.1:9119 locally.
```

The SSH server on the VPS must be **keys-only** (see §5) before you rely on
this.

---

## 3. Public endpoints: Caddy 2

Only if you need the gateway reachable over public DNS (e.g. a Telegram webhook
or a shareable dashboard) do you front it. Caddy 2 gives automatic
Let's Encrypt + optional basic auth + reverse proxy to loopback:

```bash
sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```caddyfile
hermes.example.com {
    # Optional: gate the dashboard behind basic auth. Generate the hash with
    #   caddy hash-password --plaintext 'choose-a-strong-password'
    basic_auth {
        hermes $2a$14$REPLACE_WITH_HASH_OUTPUT
    }

    reverse_proxy 127.0.0.1:9119
}
```

```bash
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Caddy auto-provisions and renews the certificate and only ever forwards to
`127.0.0.1`. If the endpoint is only for webhooks with a secret token, prefer
Tailscale (§2A) plus a reverse-proxy path check instead of opening it up.

---

## 4. Hardened systemd unit

Run the gateway as a dedicated, non-root, non-login user under a sandboxed
systemd unit. Environment secrets go in a `mode 600` file, never inline.

`/etc/systemd/system/hermes-gateway.service`:

```ini
[Unit]
Description=Hermes Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hermes
Group=hermes
# Secrets live in a root-only file, loaded by systemd (see below).
EnvironmentFile=/etc/hermes/hermes.env
WorkingDirectory=/opt/hermes
ExecStart=/opt/hermes/venv/bin/python -m hermes_cli.main gateway run --replace
Restart=on-failure
RestartSec=5

# --- hardening ---------------------------------------------------------
# No privilege escalation, ever.
NoNewPrivileges=true
# Read-only filesystem except for the dirs the gateway must write.
ProtectSystem=strict
ReadWritePaths=/opt/hermes/var /var/lib/hermes
# Private, empty /tmp and /var/tmp.
PrivateTmp=true
# No device access, no kernel tunables, no module loading.
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
# Private /home and /proc hide.
ProtectHome=true
ProtectProc=invisible
# Only loopback + outbound.
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
# Seccomp + capabilities drop.
SystemCallArchitectures=native
CapabilityBoundingSet=
LockPersonality=true
MemoryDenyWriteExecute=true

[Install]
WantedBy=multi-user.target
```

Create the runtime pieces:

```bash
sudo useradd --system --home /opt/hermes --shell /usr/sbin/nologin hermes
sudo install -d -o hermes -g hermes -m 750 /opt/hermes/var /var/lib/hermes
sudo install -d -m 700 /etc/hermes
sudo install -m 600 /dev/stdin /etc/hermes/hermes.env <<'EOF'
# Secrets ONLY. Behavioral settings belong in config.yaml, not here.
EOF
# now `sudo -e /etc/hermes/hermes.env` and add keys
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-gateway
```

Notes:

- `EnvironmentFile` is root-owned `600` so `hermes` reads it only via the
  systemd-managed environment, never directly.
- `ProtectSystem=strict` makes `/usr`, `/etc`, `/boot` read-only; declare the
  exact writable paths in `ReadWritePaths` and nothing more.
- If the unit fails to start, read `journalctl -u hermes-gateway -e` — a
  missing `ReadWritePaths` entry is the usual cause.

---

## 5. Firewall, SSH, and intrusion prevention

### ufw

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
# 22 for SSH, plus only the public port(s) you actually serve:
sudo ufw allow 22/tcp
sudo ufw allow 443/tcp     # only if you opened a Caddy public endpoint
# sudo ufw allow 51820/udp # only if you run a Tailscale *exit/subnet* node
sudo ufw enable
sudo ufw status verbose
```

Keep the allowlist minimal: with Tailscale you do **not** need to open `443`
at all.

### SSH hardening (keys-only)

In `/etc/ssh/sshd_config` (or a drop-in under `/etc/ssh/sshd_config.d/`):

```sshconfig
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
```

```bash
sudo systemctl reload ssh
```

Verify a key login still works **before** closing your current session.

### fail2ban

```bash
sudo apt install -y fail2ban
# SSH jail is on by default; confirm:
sudo fail2ban-client status sshd
```

Add a Caddy jail only if you exposed a public endpoint:

```ini
# /etc/fail2ban/jail.local
[caddy-auth]
enabled  = true
port     = http,https
filter   = caddy-auth
logpath  = /var/log/caddy/access.log
maxretry = 10
findtime = 10m
bantime  = 1h
```

### unattended-upgrades

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # answer "Yes"
# Verify:
systemctl status unattended-upgrades
```

---

## 6. Verification checklist

```bash
# 1. Loopback-only listeners
ss -tlnp | grep -E '9119'          # 127.0.0.1 only

# 2. Tailscale reachable (from your laptop)
curl -fsS https://<tailnet-name>/health || echo "tunnel down"

# 3. Public endpoint (only if you opened one) behind TLS + basic auth
curl -fsS -u hermes https://hermes.example.com/health

# 4. Unit is active and hardened
systemctl is-active hermes-gateway
systemctl show hermes-gateway -p NoNewPrivileges -p ProtectSystem -p PrivateTmp

# 5. Firewall is enforcing
sudo ufw status

# 6. fail2ban is jailing
sudo fail2ban-client status
```

## Related

- [network-egress-isolation.md](network-egress-isolation.md) — iron-proxy egress
  isolation and the SSRF deny list (complements this guide's transport layer).
- [../../SECURITY.md](../../SECURITY.md) — trust model and vulnerability reporting.
