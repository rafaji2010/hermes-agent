# Hermes Mobile (Android)

A native Android client for [Hermes](../README.md). This directory currently
contains the **foundation only** — the architecture for connecting to a Hermes
backend, the typed gateway contract the client will consume, and a buildable
(but not yet wired) Android project shell. No gateway wiring, no network calls,
no secrets.

The client talks to the **same gateway protocol the desktop app uses** — the
JSON-RPC 2.0-over-WebSocket surface served by `tui_gateway/` at `/api/ws`. It is
not a parallel protocol. The source of truth for every shape in
[`src/gateway-types.ts`](src/gateway-types.ts) is:

- `tui_gateway/server.py`, `tui_gateway/methods_session.py`,
  `tui_gateway/methods_prompt.py` — the RPC methods and streamed event payloads.
- `apps/desktop/src/types/hermes.ts` — the desktop renderer's message/session
  types (Hermes Mobile mirrors these; it does not redefine them).
- `apps/shared/src/json-rpc-gateway.ts` — the transport (frame shape, connect
  handshake, event dispatch).
- `apps/shared/src/websocket-url.ts` — how a client derives a gateway WS URL and
  authenticates (`?token=…` for token-mode backends, OAuth-minted tickets for
  remote/cloud backends).

---

## How Hermes Mobile connects to a backend

Hermes' hardened deployment posture is **loopback-first**: every listener binds
`127.0.0.1` and is reached only over an encrypted tunnel
([`docs/security/vps-deployment.md`](../docs/security/vps-deployment.md)). The
phone is a *client*, so it never sees the raw listener — it reaches it through
the same two tunnel shapes that guide the rest of the product.

### Topology 1 — local machine / homelab over Tailscale (the default)

A backend already running on a laptop or home server is reachable from the phone
over your tailnet, exactly like the desktop app's remote gateway:

```bash
# On the machine running Hermes — the gateway stays on 127.0.0.1:9119.
# Serve it to your tailnet over TLS (Tailscale-managed cert):
sudo tailscale serve --bg --https=443 http://127.0.0.1:9119
```

The phone opens `wss://<tailnet-name>/api/ws?token=<gateway_token>`. Tailscale
terminates TLS with a real certificate and only accepts traffic from your
tailnet — no public DNS, no open firewall port. This is the loopback-first
stance applied to mobile: nothing about the backend is ever exposed to the
public internet.

### Topology 2 — VPS via Tailscale (deferred)

For a backend on a VPS, the phone joins the same tailnet the VPS is on and hits
the loopback service over the tailnet's encrypted tunnel. The VPS hardening from
`vps-deployment.md` still applies in full: loopback-only listeners, dedicated
non-root systemd unit with `ProtectSystem=strict`, keys-only SSH, `ufw` with no
open ports other than the tailnet. `tailscale serve` on the VPS terminates TLS;
the phone connects with `wss://…/api/ws?token=…`.

An SSH local tunnel (`ssh -N -L 9119:127.0.0.1:9119`) is the desktop-documented
alternative, but it needs a persistent tunneled port on the phone and is not a
reasonable mobile story — Tailscale is the primary mobile path.

### Topology 3 — public endpoint behind a reverse proxy (last resort)

Only for the rare case where the phone must reach the backend without a tailnet
(e.g. a guest device). Front the loopback service with Caddy exactly as
`vps-deployment.md` describes: automatic Let's Encrypt TLS, optional basic auth,
`reverse_proxy 127.0.0.1:9119`. Mobile still authenticates with the gateway
token over `wss://hermes.example.com/api/ws?token=…`. Deferred — not built.

### Authentication

Token-mode backends authenticate the WebSocket with the gateway token passed as
a query parameter (`/api/ws?token=<token>`), the same mechanism the desktop and
dashboard use (`apps/shared/src/websocket-url.ts`). OAuth/cloud backends mint
single-use tickets per dial instead. Hermes Mobile must:

- Accept the endpoint (`wss://…`) + token pair as the whole of the connection
  configuration — never a raw `HERMES_HOME`, never backend env vars.
- Store the token in the Android Keystore-backed credential store
  (`EncryptedSharedPreferences` or the Keystore directly), never plaintext.
- Treat a confirmed 401/403 from the socket as "reauthenticate" and every
  timeout / network / server failure as a connectivity error (mirrors the
  desktop's auth corollary in `apps/desktop/AGENTS.md`).

### Security stance (mirrors `docs/security/vps-deployment.md`)

- **Loopback-first transport.** Backends stay bound to `127.0.0.1`; the phone
  only ever sees a TLS-terminated, tunneled endpoint. Never a `0.0.0.0` bind.
- **TLS always.** Tailscale certs or Let's Encrypt via Caddy. The app disables
  cleartext HTTP (`android:usesCleartextTraffic="false"`) and ships a Network
  Security Config that only permits the configured gateway host.
- **Secrets live in the backend, not the app.** The `.env` on the backend holds
  API keys (mode `600`); the phone holds only the gateway token needed to
  connect, in the Keystore.
- **Session capability, not process env.** Any surface capability (e.g. desktop
  panes, reactions) is resolved from the session's own source (`session.create`
  with `source: 'mobile'`), never from an env var on the backend process — per
  the root `AGENTS.md` rule "Surface capability is a property of the SESSION".
- **Command-approval floor stays intact.** The blocking `approval.request` /
  `clarify.request` / `sudo.request` / `secret.request` bridges are part of the
  contract the client must render and answer (`*.respond`), so dangerous
  operations remain gated on an explicit human decision on the phone.

---

## Gateway contract

[`src/gateway-types.ts`](src/gateway-types.ts) is the single typed contract file
for the mobile client. It describes, against the desktop app's real wire
shapes:

- **Transport** — the JSON-RPC 2.0 frame, `ConnectionState`, and the
  `gateway.ready` handshake the server emits on accept.
- **Events** — the streamed payloads a client renders: `message.start/delta/
  interim/complete`, `reasoning.delta`, `thinking.delta`, `tool.start/progress/
  complete`, `status.update`, `session.info`, `session.title`, and the blocking
  `*.request` events.
- **Data shapes** — `SessionMessage`, `SessionInfo`, `SessionRuntimeInfo`,
  `UsageStats`, and the create/resume/list responses, mirrored from
  `apps/desktop/src/types/hermes.ts`.
- **RPC method catalog** — the typed request params and responses for the
  methods a mobile client needs (`session.create`, `session.resume`,
  `session.list`, `prompt.submit`, `session.interrupt`, `session.title`,
  `session.usage`, `session.status`, `session.delete`, the `*.respond`
  resolvers, …).

The file is dependency-free on purpose so it can be typechecked in isolation
(see below) and, later, shared verbatim with a Kotlin/OkHttp port or an MCP
shim without pulling in the desktop app.

---

## Android project skeleton

`android/` is a minimal, structurally complete Android app: Gradle build files
(Kotlin DSL), a single `MainActivity` that renders a placeholder "Hermes
Mobile" screen, and the manifest/resources needed to compile and launch. It has
**no gateway wiring yet** — it exists to prove the project builds.

```
android/
├── settings.gradle.kts
├── build.gradle.kts
├── gradle.properties
├── gradle/wrapper/gradle-wrapper.properties
└── app/
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/hermes/mobile/MainActivity.kt
        └── res/
            ├── drawable/ic_launcher_foreground.xml   (vector — no binary assets)
            ├── layout/activity_main.xml
            ├── mipmap-anydpi-v26/ic_launcher.xml     (adaptive icon)
            └── values/{strings,colors,themes}.xml
```

Intentional choices:

- **No `INTERNET` permission yet.** The app is a shell; the permission (and the
  Network Security Config / certificate pinning) arrives with the gateway
  client so there is never a committed app that can talk to the network.
- **No third-party AndroidX/Material dependencies.** `MainActivity` extends the
  framework `Activity` and the theme is `android:Theme.Material.Light.NoActionBar`
  — the shell compiles with only the Kotlin stdlib, keeping the skeleton
  dependency-light until the real UI lands.
- **Adaptive launcher icon only** (`mipmap-anydpi-v26`, vector foreground) with
  `minSdk = 26`, so no PNG binaries are committed.
- **`android:allowBackup="false"`** so no Android backup transport can carry
  app data off the device before the credential story is built.

### Building

This workspace has no JDK, Android SDK, or Gradle, so the Android build could
**not** be run here. The files are written to be buildable on any machine with:

- JDK 17+
- Android SDK with `platforms;android-35` and `build-tools` (set `ANDROID_HOME`
  or add a `android/local.properties` with `sdk.dir=…`)
- Gradle 8.7 (or run `gradle wrapper` in `android/` first to generate
  `gradle-wrapper.jar`, then use `./gradlew`)

```bash
cd mobile/android
./gradlew :app:assembleDebug        # APK at app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:lintDebug            # static analysis
```

### Typechecking the TypeScript contract

From the repo root (the root `node_modules` already has `tsc`):

```bash
npx tsc --noEmit -p mobile/tsconfig.json
```

---

## Scope and deferrals

**In scope now:** architecture doc, typed gateway contract, compilable Android
shell. **Explicitly deferred (M14.1+):** VPS/cloud deployment docs and
tooling, the gateway client implementation (WebSocket transport, session
lifecycle, streaming transcript), tool-call rendering, notifications, local
credential UX, and store/keystore integration. Each deferred piece has its
contract seam already described here and in `src/gateway-types.ts`.
