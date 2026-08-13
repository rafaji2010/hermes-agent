# Distributed Agent: Desktop ↔ VPS ↔ Mobile

This document specifies the **distributed-agent topology** for Hermes: a
self-hosted VPS running the Hermes gateway as the always-on control plane,
with the desktop/CLI and Hermes Mobile as **clients** that attach to it.
It is an architecture and contract document, not a deployment runbook —
standing up the VPS is deferred. Operational hardening lives in
[`docs/security/vps-deployment.md`](../security/vps-deployment.md); this
document is the layer above it: *which surface connects how, who authenticates
how, and who is allowed to be right about what.*

The invariant this whole design serves: **loopback-first, tunnel second,
public last.** Every Hermes listener stays on `127.0.0.1`; remote access is
layered on top with the narrowest exposure that still works, and identity at
every rung is the existing gateway auth — never a new protocol.

## 1. Topology

```
┌──────────────────────┐        ┌───────────────────────────────────────────┐
│  Hermes Desktop /    │        │  VPS — Hermes gateway (always-on)         │
│  CLI (laptop)        │        │                                           │
│  ─────────────────   │        │  ┌─────────────────────────────────────┐  │
│  · native OAuth      │        │  │ gateway (hermes gateway run)        │  │
│    (RFC 8252, PKCE)  │        │  │  · sessions, tools, model calls     │  │
│  · keychain tokens   │        │  │  · sessions DB (SQLite)             │  │
│  · cached ws-ticket  │        │  │  · prompt cache (per-conversation)  │  │
│    per dial          │        │  └─────────────────────────────────────┘  │
└──────────┬───────────┘        │  ┌─────────────────────────────────────┐  │
           │  rung 2: Tailscale │  │ dashboard / API server (FastAPI)    │  │
           │  rung 3: SSH -L    │  │  · loopback-only bind 127.0.0.1:9119│  │
           │  rung 4: Caddy TLS │  │  · OAuth gate (auth_required)       │  │
           ▼                    │  │  · WS ticket mint (POST /api/auth/   │  │
┌──────────────────────┐        │  │    ws-ticket → ?ticket=, single-use)│  │
│  Hermes Mobile       │        │  └─────────────────────────────────────┘  │
│  ─────────────────   │        │  · message gateway (Telegram, Discord,   │
│  · app (React Native)│        │    Slack, …) — outbound to platforms     │
│  · same gateway auth │        │  · cron scheduler                         │
│  · same access ladder│        │  · egress allowlist (iron-proxy posture)  │
└──────────────────────┘        └───────────────────────────────────────────┘
```

Three roles, and they are not symmetric:

- **The VPS gateway is the authority and the workhorse.** It owns sessions,
  state, tools, and model calls. It is the only always-on peer — mobile
  notifications, message-gateway delivery, and cron jobs are its job, not the
  laptop's.
- **Desktop/CLI is a client, but a first-class one.** It runs the same
  RFC 8252 native OAuth flow the desktop app already uses, and keeps a
  keychain token so it can mint fresh WS credentials on every dial.
- **Mobile is a client, and a thin one.** It attaches through the same
  identity and the same access ladder, with the least authority of the three
  (see §4). It is a cache with a composer, not a second control plane.

Every remote path terminates at the **loopback listener on the VPS**. No
client ever talks to a public gateway socket directly — that is the whole
point of the access ladder.

## 2. Access ladder (loopback-first)

Access is an **ordered ladder of candidates**, mirroring the desktop's
"observable ladder" pattern (precedence written down in one place, each rung
validated before it is trusted, failure falls to the next rung). The ladder is
implemented as a pure data structure in
[`mobile/src/connection-ladder.ts`](../../mobile/src/connection-ladder.ts).

| Rank | Rung | Applies when | Remote? | Public? |
|------|------|--------------|---------|---------|
| 1 | **Local loopback** (`127.0.0.1:9119`) | Same machine as the gateway (dev, LAN-only use) | No | No |
| 2 | **Tailscale** (`tailscale serve --bg --https=443 http://127.0.0.1:9119`) | Preferred remote: your own devices on your tailnet | Yes | No (tailnet only) |
| 3 | **SSH tunnel** (`ssh -N -L 9119:127.0.0.1:9119 hermes@VPS`) | Tailscale unavailable (no tailnet, restricted network, ad-hoc) | Yes | No |
| 4 | **Caddy reverse-proxy** (public DNS, `basic_auth` + auto-TLS) | You deliberately need public reachability (webhook target, shareable dashboard) | Yes | Yes |

### Rank semantics

- **1 — Loopback.** No network trust boundary; OS-level user-account
  protection is the authorization (SECURITY.md §2.6 uniform rule 1). Use when
  the gateway runs on the same machine as the client.
- **2 — Tailscale (preferred remote).** Terminates TLS with a
  Tailscale-managed cert, traffic only from your tailnet, no public DNS, no
  open firewall port. This is the default answer to "reach my VPS from my
  laptop / phone." `tailscale serve` exposes the *loopback* listener, so the
  gateway never widens its bind.
- **3 — SSH tunnel (fallback remote).** No extra daemon, works through
  restrictive networks, reuses the VPS's keys-only SSH. `ssh -N -L` forwards
  the remote loopback to a local port; the client then talks to
  `127.0.0.1:<local-port>` exactly as if local. Requires the VPS SSH server be
  keys-only (`PasswordAuthentication no`).
- **4 — Caddy (public, last resort).** Only when a public endpoint is
  genuinely required. Caddy auto-provisions Let's Encrypt, gates with
  `basic_auth`, and `reverse_proxy`s to `127.0.0.1:9119` — the gateway still
  never binds publicly. If the need is just a webhook, prefer Tailscale plus
  a path check instead (§5 of the VPS guide).

**Precedence rule:** prefer the highest rung that actually reaches the
gateway. "Reaches" means the full connection, not a status probe — see
§3's corollary about testing the leg you actually use. A rung is skipped only
on a *validation failure* (unreachable, wrong identity), never on a transient
blip, which retries on the same rung.

**Auth at every rung:** the transport rung selects *how* you reach the
loopback; it does **not** replace gateway identity. Every rung — even
loopback in gated mode — still authenticates to the gateway (see §3). The
ladder ranks transport, not trust.

## 3. Identity & auth

Identity is the gateway's existing OAuth gate — mirrored from the desktop
app's gateway connection code (`apps/desktop/electron/connection-config.ts`,
`apps/desktop/src/lib/gateway-ws-url.ts`, and the shared
`apps/shared/src/websocket-url.ts`). No new auth, no new protocol.

### The two modes (existing contract)

The gateway advertises its auth shape via the public `GET /api/status`
(`auth_required: true` → OAuth gate engaged). Clients classify into the two
supported modes:

- **`oauth` (gated gateways — the remote/VPS default).** The client runs the
  RFC 8252 native-app flow against the gateway as the authorization server
  (`/auth/native/authorize` → system browser → loopback `?code=` redirect →
  `POST /auth/native/token` with PKCE). The gateway brokers the upstream IDP;
  it is the authorization server *to the client*, and an OAuth client *to the
  portal* (see `hermes_cli/dashboard_auth/native_flow.py`).
- **`token` (legacy loopback mode).** A static dashboard session token;
  REST uses `X-Hermes-Session-Token`, WS uses `?token=`. This is the *lower
  rung* — used for the local/loopback case, never for a remotely-exposed
  gateway.

### One-time credentials are never reused

This is the desktop AGENTS.md corollary, adopted verbatim because it is the
safety property of the whole design:

- An **OAuth** connection **mints a fresh WebSocket ticket on every dial**
  (`POST /api/auth/ws-ticket` → single-use, 30-second TTL → `?ticket=` on the
  WS upgrade) and **never falls back to a cached URL**. A leaked ticket is
  uninteresting by construction.
- Only a **confirmed 401/403** (or an explicitly `needsOauthLogin`-tagged
  rejection) means reauthentication. Timeout, network, malformed-response, and
  server failures remain **connectivity errors** — retry on the same rung, do
  not bounce the user to sign-in.
- Only **long-lived token/local auth** may reuse a cached URL as a lower rung.
  The token may be stored (desktop keychain, mobile secure storage); tickets
  never are.

The shared `resolveGatewayWsUrl` contract encodes exactly this: OAuth mode
throws a `GatewayReauthRequiredError` on an auth rejection, keeps transport
failures retryable, and never returns the stale cached ticket.

### Authentication by surface

| Surface | REST auth | WS auth (each dial) | Token storage |
|---------|-----------|---------------------|---------------|
| Desktop | `Authorization: Bearer <access_token>` (native OAuth, no cookies) | fresh `?ticket=` minted per dial | OS keychain (AT + rotating RT) |
| CLI | same native flow (system browser) | fresh `?ticket=` per dial | local secure store |
| Mobile | same native flow (system browser / ASWebAuthenticationSession) | fresh `?ticket=` per dial | platform secure storage (Keychain/Keystore) |

Note the deliberate property: **no surface stores or reuses a ws-ticket.**
The ticket is minted, used once, discarded. Only the *token* is long-lived,
and it is never sent over the wire except inside the RFC 8252 token exchange
(and the desktop's `/auth/native/refresh` rotation).

### State at the boundary

Clients must not assume a socket that *was* open still is. Reconnect always
re-mints a ticket; a cached URL is never dialed in OAuth mode. Connection
state is per-session; a re-home (see §4) re-runs the ladder and the mint, in
that order.

## 4. State authority

From the desktop AGENTS.md "Decide state by authority": the first question for
any piece of state is *who is allowed to be right about it*, not where it is
convenient to store it. In the distributed topology there are three parties
and one authority:

| State | Authority | Mobile/Desktop role |
|-------|-----------|---------------------|
| Sessions, conversation history, tools, model calls, memory, skills, cron jobs, gateway delivery | **VPS gateway (backend)** | Cache, keyed by the backend's stable session identity |
| Connection config (gateway URL, auth mode, which rung) | **Client (desktop/mobile)** — it owns how it connects | Owned locally; never written back to the backend |
| Machine/runtime facts (device, OS, local files, notifications) | **Client device** | Owned locally |
| Pure presentation (which screen is open, scroll position, ephemeral interaction) | **Client UI** | Owned locally, dropped on re-home |

**The backend is authoritative for anything another surface can also change.**
The desktop renderer's copy — and the mobile app's copy — are caches of that
truth. Clients reconcile, they do not own:

- **Merge, don't clobber.** A refresh layers new information over what the
  client already knows; it must never drop live or pinned rows.
- **Be optimistic, then honest.** Direct manipulation paints immediately from
  a snapshot; a failed write rolls back visibly, and an authoritative refresh
  gets the last word.
- **Guard against the past.** Async results arrive out of order; a stale
  response must never overwrite newer intent.
- **Isolate the foreground.** Only the surface the user is looking at
  publishes into the shared view; background work updates its own cache
  quietly.

**Re-home is a re-home, not a reboot.** Switching connection rung, profile, or
backend is a workspace switch: the shell stays put, the gateway-bound view is
cleared and repopulated, and the previous context must not leak into the next.
A rung failure on a live session re-runs the ladder; it never fabricates
state to paper over the gap.

Mobile is a cache with a composer, and is scoped the same way: its session
list, transcripts, and streaming views are all keyed off the backend's stable
session identity, translated at the boundary (the desktop "identity is not
incidental" rule applies: durable identity for anything pinned or persisted,
runtime identity for live streaming).

## 5. Security

The P1/P2/P3 stance from `docs/security/` (P1 = load-bearing boundary /
must-fix, P2 = significant degradation, P3 = hardening/defense-in-depth):

### P1 — the OS boundary and loopback binds

- **Loopback bindings are load-bearing.** The gateway and dashboard listeners
  stay on `127.0.0.1` (`ss -tlnp | grep 9119` must show `127.0.0.1`, never
  `0.0.0.0`). Binding a local-only surface to a non-loopback interface is a
  break-glass operator decision (SECURITY.md §2.6 rule 5) that moves the
  surface into §4 public-exposure territory. The access ladder is the
  sanctioned way to go remote; widening the bind is not.
- **No secrets in committed files.** Nothing here, and nothing in the
  deployed config, contains real credentials. Secrets live in a root-owned
  `mode 600` environment file on the VPS (VPS guide §4); client tokens live in
  OS keychains/keystores. Committed configs carry placeholders only.
- **Prompt injection is data, not instructions.** Per SECURITY.md §3.2,
  getting the LLM to emit unusual output is not itself a vulnerability — but
  an injected payload *chaining to an outcome* (credential exfiltration,
  unauthorized tool use, cross-surface state corruption) is in scope. The
  distributed topology must not create a new injection amplifier: the message
  gateway and any public webhook surface are untrusted input surfaces, which
  per SECURITY.md §2.2 moves the operator to the whole-process-wrapping
  posture. A desktop/mobile client must treat any gateway-rendered content as
  data; a payload in one surface must never be able to steer another surface's
  state without crossing the same authorization checks.

### P2 — authorization at every trust boundary

- Authorization is required at **every surface that crosses a trust
  boundary** (SECURITY.md §2.6 rule 1): every network rung still authenticates
  to the gateway (§3). An allowlist is required for every network-exposed
  adapter; adapters must refuse to dispatch work until one is set (rule 2).
- Session identifiers are **routing handles, not authorization boundaries**
  (rule 3). Knowing another surface's session ID grants nothing; identity is
  re-checked at the gateway.
- Within the authorized set, all callers are equally trusted (rule 4) —
  which is exactly why the gateway's command-approval floor and egress
  isolation stay enforced in the core, not by the transport layer.

### P3 — defense in depth

- **Single-use WS tickets** (30s TTL, consumed on first use) make a leaked
  ticket uninteresting. The process-lifetime internal credential that
  authenticates server-spawned children is never injected into any HTML/SPA —
  it only ever leaves the process via a spawned child's environment
  (`hermes_cli/dashboard_auth/ws_tickets.py`). The same rule applies to any
  mobile/desktop surface: never put a long-lived credential in a place a
  script can read.
- **PKCE + state** on every native login (RFC 7636): a stolen loopback
  `?code=` is not redeemable without the verifier, which never leaves the
  client.
- **Minimal public surface.** Caddy (`basic_auth` + auto-TLS) fronts
  loopback; firewall allows only `22` and (if opened) `443`; Tailscale needs
  no public port at all. fail2ban jails the SSH and (if public) Caddy.
- **Egress isolation.** The VPS gateway runs with the iron-proxy egress
  posture from `docs/security/network-egress-isolation.md`: outbound traffic
  goes through an allowlisted proxy, so a prompt-injection payload that
  reaches a tool cannot exfiltrate to an arbitrary host.

### Degradation semantics

Falling down the ladder degrades *reachability*, never *safety*: every rung
still authenticates, still mints fresh tickets, still treats injected content
as data. The only thing a lower rung changes is the transport path to the same
loopback authority.

## Related

- [`docs/security/vps-deployment.md`](../security/vps-deployment.md) — the
  operations guide this document's transport ladder is built on.
- [`docs/security/network-egress-isolation.md`](../security/network-egress-isolation.md) —
  egress isolation and the SSRF deny list.
- [`SECURITY.md`](../../SECURITY.md) — the trust model, the §2.2 OS boundary,
  and the §3 vulnerability scope (P1/P2/P3 semantics).
- [`apps/desktop/AGENTS.md`](../../apps/desktop/AGENTS.md) — "Decide state by
  authority" and "Cross everything as an observable ladder," the two patterns
  this design mirrors.
- `apps/desktop/electron/connection-config.ts`, `apps/shared/src/websocket-url.ts`,
  `hermes_cli/dashboard_auth/{native_flow,ws_tickets}.py` — the auth contract
  this design reuses, never re-invents.
- [`mobile/src/connection-ladder.ts`](../../mobile/src/connection-ladder.ts) —
  the connection ladder as an ordered, validated candidate list.
