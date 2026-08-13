/**
 * connection-ladder.ts
 *
 * The Hermes access ladder for the Desktop ↔ VPS ↔ Mobile topology, as a pure
 * data structure. Mirrors the desktop's "observable ladder" pattern (see
 * apps/desktop/AGENTS.md — "Cross everything as an observable ladder"): the
 * precedence is written down in ONE place, a candidate is trusted only after
 * it is validated at the boundary, a failed read falls to the next rung, and
 * a transient failure retries on the SAME rung rather than silently
 * retargeting.
 *
 * This module is deliberately standalone and dependency-free: it is the
 * transport-side contract that mirrors the desktop's `resolveGatewayWsUrl`
 * (apps/shared/src/websocket-url.ts) and the auth contract in
 * `hermes_cli/dashboard_auth/`. It decides WHICH transport rung to try next —
 * it never decides who the client is (that is the gateway's OAuth gate) and it
 * never carries a credential. See docs/architecture/distributed-agent.md §2-3.
 */

/** How a client intends to reach the gateway's loopback listener. */
export type ConnectionRung =
  | 'loopback'
  | 'tailscale'
  | 'ssh-tunnel'
  | 'caddy-public'

/**
 * A single candidate on the ladder, in its resolved, ready-to-validate form.
 * The URL is the candidate's ws(s) base — auth is appended by the caller per
 * the gateway auth contract (never reuse a one-time ticket; mint per dial).
 */
export interface ConnectionCandidate {
  /** Rank on the ladder; 1 = loopback (first), 4 = caddy-public (last). */
  rank: 1 | 2 | 3 | 4
  /** The transport rung this candidate belongs to. */
  rung: ConnectionRung
  /** Transport-selected base URL, e.g. "wss://<tailnet-name>/api/ws". */
  url: string
  /** True when this rung exposes the gateway to the public internet. */
  isPublic: boolean
  /** True when this rung is a remote (non-loopback) transport. */
  isRemote: boolean
  /**
   * Highest rank that is allowed to participate. A profile configured for
   * "private only" caps the ladder at the SSH rung so Caddy never becomes a
   * candidate even when a public URL is configured.
   */
  maxRank: 1 | 2 | 3 | 4
}

export interface ConnectionLadderOptions {
  /**
   * How the client is configured to reach the gateway. Controls which rungs
   * are allowed to participate:
   *  - 'local'  → loopback only.
   *  - 'tailnet' → loopback + tailscale (preferred remote).
   *  - 'remote' → loopback + tailscale + ssh-tunnel.
   *  - 'public' → all rungs, including caddy-public.
   */
  mode: 'local' | 'tailnet' | 'remote' | 'public'
  /** Loopback base URL. Never empty — every profile has a loopback rung. */
  loopbackUrl: string
  /** Tailscale HTTPS base URL (tailnet name, e.g. "my-vps.tailnet.ts.net"). */
  tailscaleUrl?: string
  /** Remote loopback port as reached through the SSH tunnel. */
  sshLocalPort?: number
  /** Public base URL, only ever used when mode is 'public'. */
  publicUrl?: string
}

/** Rank → rung, the single written-down precedence. */
const RUNG_BY_RANK = {
  1: 'loopback',
  2: 'tailscale',
  3: 'ssh-tunnel',
  4: 'caddy-public'
} as const

const RANK_OF_RUNG = {
  loopback: 1,
  tailscale: 2,
  'ssh-tunnel': 3,
  'caddy-public': 4
} as const

const MAX_RANK_BY_MODE = {
  local: 1,
  tailnet: 2,
  remote: 3,
  public: 4
} as const

/** First rung the candidate list tries for a given configured mode. */
const FIRST_RUNG_BY_MODE = {
  local: 1,
  tailnet: 2,
  remote: 2,
  public: 2
} as const

/**
 * The connection ladder as an ordered candidate list. Candidates are ordered
 * loopback-first, and are limited by the configured mode. The caller validates
 * each candidate at its boundary (open the socket, confirm the upgrade — never
 * trust a bare HTTP status probe, per the desktop's auth corollary) and falls
 * to the next candidate only on a validation failure; transient errors retry
 * on the same candidate.
 */
export function buildConnectionLadder(options: ConnectionLadderOptions): ConnectionCandidate[] {
  const maxRank = MAX_RANK_BY_MODE[options.mode]
  const firstRank = FIRST_RUNG_BY_MODE[options.mode]
  const candidates: ConnectionCandidate[] = []

  for (let rank: number = firstRank; rank <= maxRank; rank += 1) {
    const rung = RUNG_BY_RANK[rank as 1 | 2 | 3 | 4]

    if (rung === 'loopback') {
      candidates.push({
        rank: 1,
        rung: 'loopback',
        url: options.loopbackUrl,
        isPublic: false,
        isRemote: false,
        maxRank
      })

      continue
    }

    if (rung === 'tailscale') {
      if (!options.tailscaleUrl) {
        continue
      }

      candidates.push({
        rank: 2,
        rung: 'tailscale',
        url: options.tailscaleUrl,
        isPublic: false,
        isRemote: true,
        maxRank
      })

      continue
    }

    if (rung === 'ssh-tunnel') {
      const port = options.sshLocalPort

      if (!port) {
        continue
      }

      candidates.push({
        rank: 3,
        rung: 'ssh-tunnel',
        url: `ws://127.0.0.1:${port}/api/ws`,
        isPublic: false,
        isRemote: true,
        maxRank
      })

      continue
    }

    if (options.publicUrl) {
      candidates.push({
        rank: 4,
        rung: 'caddy-public',
        url: options.publicUrl,
        isPublic: true,
        isRemote: true,
        maxRank
      })
    }
  }

  return candidates
}

/**
 * The configured mode caps the ladder at a maximum rank. A profile that is
 * "private only" must never fall through to a public rung, even when a public
 * URL is configured — this is the single written-down enforcement of that
 * precedence.
 */
export function maxRankForMode(mode: ConnectionLadderOptions['mode']): 1 | 2 | 3 | 4 {
  return MAX_RANK_BY_MODE[mode]
}

/** Rank of a rung. Useful for reporting and for tests. */
export function rankOfRung(rung: ConnectionRung): 1 | 2 | 3 | 4 {
  return RANK_OF_RUNG[rung]
}

/**
 * True when a rung is allowed by the configured mode. Precedence is enforced
 * here, in one place, so a caller asking "may I use the public rung?" gets the
 * same answer as the ladder itself.
 */
export function isRungAllowed(
  rung: ConnectionRung,
  mode: ConnectionLadderOptions['mode']
): boolean {
  return rankOfRung(rung) <= maxRankForMode(mode)
}
