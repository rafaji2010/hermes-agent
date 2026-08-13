/**
 * Hermes Mobile — gateway client contract.
 *
 * The typed wire contract Hermes Mobile consumes from a Hermes backend. This is
 * a MIRROR of the desktop app's real shapes, not a parallel protocol:
 *
 *   - Transport / frames        → apps/shared/src/json-rpc-gateway.ts
 *   - WS URL + auth             → apps/shared/src/websocket-url.ts
 *   - Message / session types   → apps/desktop/src/types/hermes.ts
 *   - RPC methods + event emit  → tui_gateway/{server,methods_session,methods_prompt}.py
 *
 * Backends predate this app, so every payload below is read defensively: fields
 * are optional where an older backend may omit them, and readers must narrow
 * before indexing into unknown-shaped fields (mirrors the desktop's rules).
 *
 * The file is intentionally dependency-free so it typechecks standalone and can
 * later be ported verbatim (e.g. to Kotlin/OkHttp) without pulling in the
 * desktop app.
 */

/* ===========================================================================
 * Transport
 * ==========================================================================*/

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'error'
export type GatewayRequestId = number | string

/**
 * Server streamed-event names. Mirrors `GatewayEventName` in
 * apps/shared/src/json-rpc-gateway.ts plus the server-emitted events a mobile
 * transcript must handle. `(string & {})` keeps forward compatibility with
 * events the desktop surface added later.
 */
export type GatewayEventName =
  | 'gateway.ready'
  | 'session.info'
  | 'session.title'
  | 'session.reclaimed'
  | 'sessions.changed'
  | 'message.start'
  | 'message.delta'
  | 'message.interim'
  | 'message.complete'
  | 'thinking.delta'
  | 'reasoning.delta'
  | 'reasoning.available'
  | 'status.update'
  | 'tool.start'
  | 'tool.progress'
  | 'tool.complete'
  | 'tool.generating'
  | 'clarify.request'
  | 'approval.request'
  | 'sudo.request'
  | 'secret.request'
  | 'background.complete'
  | 'subagent.start'
  | 'subagent.complete'
  | 'subagent.text'
  | 'subagent.thinking'
  | 'subagent.tool'
  | 'error'
  | 'skin.changed'
  | (string & {})

/** One event frame. `profile`/`session_id` are stamped by the server when the
 *  event belongs to a specific profile or runtime session. */
export interface GatewayEvent<P = unknown> {
  payload?: P
  profile?: string
  session_id?: string
  type: GatewayEventName
}

/** One JSON-RPC frame on the wire. Requests carry an `id` + `method`; the
 *  server replies with the same `id` + `result`/`error`, and pushes events as
 *  `{ "method": "event", "params": GatewayEvent }`. */
export interface JsonRpcFrame {
  jsonrpc?: '2.0'
  error?: { code?: number; message?: string }
  id?: GatewayRequestId | null
  method?: string
  params?: GatewayEvent
  result?: unknown
}

/**
 * `gateway.ready` is pushed immediately after the server accepts the socket
 * (tui_gateway/ws.py). Mobile should treat it as the "the gateway is up" signal
 * and gate the session boot on it, mirroring the desktop's gateway.ready use.
 */
export interface GatewayReadyPayload {
  skin?: unknown
  /** True when this backend broadcasts pet.changed / cron.changed /
   *  sessions.changed so clients can demote their legacy polls. */
  change_events?: boolean
}

/* ===========================================================================
 * Data shapes (mirrored from apps/desktop/src/types/hermes.ts)
 * ==========================================================================*/

/** Usage snapshot a session carries in message.complete / session.usage /
 *  session.info. */
export interface UsageStats {
  calls: number
  context_max?: number
  context_percent?: number
  context_used?: number
  cost_usd?: number
  input: number
  output: number
  total: number
}

/**
 * One persisted conversation message as served by the gateway (resume path) and
 * REST transcripts. A backend older than the desktop app can serve
 * `display_metadata` as unparsed JSON text and `row_id` absent — narrow before
 * indexing. Mirrors `SessionMessage` in apps/desktop/src/types/hermes.ts.
 */
export interface SessionMessage {
  args?: unknown
  content: unknown
  context?: unknown
  display_kind?: string
  display_metadata?: string | TimelineDisplayMetadata
  id?: number
  name?: string
  reasoning?: null | string
  reasoning_content?: null | string
  reasoning_details?: unknown
  role: 'assistant' | 'system' | 'tool' | 'user'
  /** Durable `messages.id` from the backend; the gateway resume path names it
   *  `row_id`, the REST transcript path ships it as `id`. */
  row_id?: number
  text?: unknown
  timestamp?: number
  tool_call_id?: null | string
  tool_calls?: unknown
  tool_name?: string
}

export type TimelineDisplayMetadata =
  | { model: string; provider?: string }
  | { delegation_id: string; task_count: number; completed_count?: number; failed_count?: number; duration_seconds?: number }
  | { reactions: MessageReaction[] }

export interface MessageReaction {
  emoji: string
  author: 'agent' | 'user'
  /** Epoch seconds. */
  at: number
}

/** A row in the session list / sidebar. Mirrors `SessionInfo` minus the
 *  desktop-only display fields; keep additions here server-sourced. */
export interface SessionInfo {
  archived?: boolean
  cwd?: null | string
  ended_at: null | number
  id: string
  input_tokens: number
  is_active: boolean
  last_active: number
  message_count: number
  model: null | string
  output_tokens: number
  parent_session_id?: null | string
  pinned?: boolean
  preview: null | string
  source: null | string
  started_at: number
  title: null | string
  tool_call_count: number
  profile?: string
  is_default_profile?: boolean
}

/** Session runtime metadata carried by `session.info` events and the resume
 *  response. Mirrors `SessionRuntimeInfo` in apps/desktop/src/types/hermes.ts. */
export interface SessionRuntimeInfo {
  approval_mode?: 'manual' | 'off' | 'smart'
  branch?: string
  cwd?: string
  fast?: boolean
  model?: string
  personality?: string
  profile_name?: string
  provider?: string
  reasoning_effort?: string
  running?: boolean
  service_tier?: string
  skills?: Record<string, string[]> | string[]
  tools?: Record<string, string[]>
  usage?: Partial<UsageStats>
  version?: string
  yolo?: boolean
}

/* ===========================================================================
 * Streamed event payloads
 * ==========================================================================*/

/** `message.start` — a turn began; payload is empty on current backends. */
export interface MessageStartPayload {
  session_id?: string
}

/** `message.delta` — streamed assistant token(s). */
export interface MessageDeltaPayload {
  text: string
  /** Server-rendered markdown when the gateway has a renderer enabled. */
  rendered?: string
}

/** `message.interim` — sealed assistant commentary that must not be lost when
 *  message.complete replaces the streaming buffer. */
export interface MessageInterimPayload {
  text: string
  already_streamed?: boolean
}

export type MessageCompleteStatus = 'complete' | 'error' | 'interrupted'

/** `message.complete` — the turn's terminal frame. */
export interface MessageCompletePayload {
  /** The final response text. Empty + `status: 'error'` + `error` carries a
   *  provider/budget failure (desktop mirrors the CLI's "Error: <detail>"). */
  text: string
  status: MessageCompleteStatus
  usage?: UsageStats
  /** Final reasoning block, when the provider produced one. */
  reasoning?: string
  rendered?: string
  warning?: string
  error?: string
  recoverable?: boolean
  failure_reason?: string
  /** Structured billing-wall descriptor when the turn hit a billing gate. */
  billing?: BillingBlock
  response_previewed?: boolean
}

/** `thinking.delta` — model reasoning narration streamed to the client. */
export interface ThinkingDeltaPayload {
  text: string
}

/** `reasoning.delta` — reasoning tokens streamed alongside a reply. */
export interface ReasoningDeltaPayload {
  text: string
}

/** `reasoning.available` — a reasoning block became available post-turn. */
export interface ReasoningAvailablePayload {
  preview?: string
  reasoning?: string
}

/** `status.update` — ephemeral status line (tool progress, goal recovery,
 *  compaction, process notices). */
export interface StatusUpdatePayload {
  kind: string
  text: string
}

/** `tool.start` — a tool call began. `args` is present when the gateway ships
 *  it live (expanded-row rendering); `context` is an 80-char display preview. */
export interface ToolStartPayload {
  tool_id: string
  name: string
  context?: string
  args?: Record<string, unknown>
  args_text?: string
}

/** `tool.progress` — live progress for long-running tools. */
export interface ToolProgressPayload {
  tool_id: string
  name: string
  text?: string
  percent?: number
}

/** `tool.complete` — the tool's result. `result` is parsed JSON when
 *  parseable, else the raw string. */
export interface ToolCompletePayload {
  tool_id: string
  name: string
  args: Record<string, unknown>
  result: unknown
  duration_s?: number
  summary?: string
  result_text?: string
  todos?: unknown[]
  inline_diff?: string
}

/** `tool.generating` — cheap "model is drafting a tool call" tick. */
export interface ToolGeneratingPayload {
  name: string
}

/** `session.info` — authoritative runtime metadata for one session, built by
 *  tui_gateway/server.py `_session_info`. */
export interface SessionInfoPayload extends SessionRuntimeInfo {
  stored_session_id?: string
  desktop_contract?: number
  title?: string
  mcp_servers?: unknown[]
  system_prompt?: string
  update_behind?: null | number
  update_command?: string
  release_date?: string
}

/** `session.title` — live rename pushed mid-conversation. */
export interface SessionTitlePayload {
  session_id: string
  title: string
}

/** `background.complete` — a background delegation finished. */
export interface BackgroundCompletePayload {
  task_id: string
  text: string
}

/** `error` — a session error surfaced to the transcript. */
export interface ErrorPayload {
  message?: string
  session_id?: string
  status?: string
}

/* ===========================================================================
 * Blocking request / respond bridge
 *
 * The agent blocks until the client answers `*.respond`. Mobile must surface
 * these as first-class input surfaces; a missing handler stalls the agent
 * until its server-side timeout.
 * ==========================================================================*/

/** `clarify.request` — the clarify tool asks a question. Answer with
 *  `clarify.respond { request_id, answer }`. */
export interface ClarifyRequestPayload {
  request_id: string
  question: string
  choices?: string[]
  /** Pass-through hint: honor multi-select when the renderer supports it. */
  multi_select?: boolean
}

/** `approval.request` — dangerous-command / execute_code approval. Answer with
 *  `approval.respond { request_id, choice }`. */
export interface ApprovalRequestPayload {
  request_id: string
  /** Redacted command (credential-shaped values are scrubbed server-side). */
  command?: string
  description?: string
  /** ['once','session','always','deny'] unless tirith forbids permanent. */
  choices?: string[]
  /** False only when a tirith warning forbids the 'always' choice. */
  allow_permanent?: boolean
  smart_denied?: boolean
}

/** `sudo.request` — sudo password capture (tools/terminal_tool.py). Answer
 *  with `sudo.respond { request_id, password }`. */
export interface SudoRequestPayload {
  request_id: string
}

/** `secret.request` — skill credential capture. Answer with
 *  `secret.respond { request_id, value }`. */
export interface SecretRequestPayload {
  request_id: string
  env_var?: string
  prompt?: string
}

/* ===========================================================================
 * RPC method catalog
 *
 * The methods Hermes Mobile needs, with their request params and response
 * shapes. `GatewayMethods` is the single table; narrow a method with
 * `GatewayParamsFor<'session.create'>` / `GatewayResponseFor<'session.resume'>`.
 * Every response below is the JSON-RPC `result` (the envelope's `error` is the
 * reject path). Mirrors tui_gateway/methods_{session,prompt,tools}.py.
 * ==========================================================================*/

export interface BillingBlock {
  provider?: string
  billing_url?: string
  is_nous?: boolean
  message?: string
}

/** `session.create` params — desktop sends source:'desktop'; mobile sends
 *  source:'mobile' so surface capability stays a property of the SESSION. */
export interface SessionCreateParams {
  cols?: number
  source?: string
  cwd?: string
  profile?: string
  model?: string
  provider?: string
  reasoning_effort?: string
  fast?: boolean
}

export interface SessionCreateResponse {
  session_id: string
  stored_session_id?: string
  info?: SessionRuntimeInfo
  message_count?: number
  messages?: SessionMessage[]
}

/** `session.resume` params — re-register a stored conversation's runtime. */
export interface SessionResumeParams {
  session_id: string
  source?: string
  profile?: string
  cols?: number
}

/** `session.resume` response — transcript + runtime state for the resumed
 *  conversation. `messages_omitted` is true when a huge history was truncated. */
export interface SessionResumeResponse {
  resumed: string
  running?: boolean
  status?: string
  session_id: string
  session_key?: string
  started_at?: number
  message_count: number
  messages: SessionMessage[]
  messages_omitted?: boolean
  info?: SessionRuntimeInfo
  inflight?: null | {
    assistant?: string
    corrections?: string[]
    error?: string
    recoverable?: boolean
    status?: string
    streaming?: boolean
    user?: string
  }
  queued?: null | { user?: string }
  auto_continue?: { attempt: number; interrupted_at: number }
}

/** `session.list` params/response — compact recent-conversation rows. */
export interface SessionListParams {
  limit?: number
}

export interface SessionListResponse {
  sessions: SessionSummary[]
}

export interface SessionSummary {
  id: string
  title: string
  preview: string
  started_at: number
  message_count: number
  source: string
}

/** `session.most_recent` — "latest eligible conversation or nothing". */
export interface SessionMostRecentParams {
  profile?: string
}

export interface SessionMostRecentResponse {
  session_id: null | string
  title?: string
  started_at?: number
  source?: string
}

/** `prompt.submit` params — send the user's text into a runtime session. The
 *  gateway answers `{ status: 'streaming' }` and the turn arrives as
 *  message.* / tool.* / status.* events. `queued` is used by the desktop's
 *  queue drain; `interrupted` marks a client-side barge-in. */
export interface PromptSubmitParams {
  session_id: string
  text: string
  interrupted?: boolean
  surface?: string
  queued?: boolean
  truncate_before_user_ordinal?: number
}

export interface PromptSubmitResponse {
  status?: 'streaming' | 'queued'
  voice_stopped?: boolean
}

/** `session.interrupt` — stop the running turn. */
export interface SessionInterruptParams {
  session_id: string
}

export interface SessionInterruptResponse {
  ok?: boolean
}

/** `session.title` — read or set a conversation's title. */
export interface SessionTitleParams {
  session_id: string
  title?: string
}

export interface SessionTitleResponse {
  title: string
  session_key: string
}

/** `session.usage` — live token/cost snapshot. */
export interface SessionUsageParams {
  session_id: string
}

export type SessionUsageResponse = Partial<UsageStats> & {
  credits_lines?: string[]
}

/** `session.status` — coarse session status line. */
export interface SessionStatusParams {
  session_id: string
  profile?: string
}

export interface SessionStatusResponse {
  session_id?: string
  running?: boolean
  [key: string]: unknown
}

/** `session.delete` — permanently delete a stored conversation. */
export interface SessionDeleteParams {
  session_id: string
  profile?: string
}

export interface SessionDeleteResponse {
  deleted: string
}

/** `session.activate` — attach to an already-live runtime session. */
export interface SessionActivateParams {
  session_id: string
}

export interface SessionActivateResponse {
  session_id?: string
}

/** `session.redirect` — send a correction into the live turn. */
export interface SessionRedirectParams {
  session_id: string
  text: string
}

export interface SessionRedirectResponse {
  ok?: boolean
}

/** `session.branch` — fork the current conversation. */
export interface SessionBranchParams {
  session_id: string
  cols?: number
  cwd?: string
  profile?: string
}

export interface SessionBranchResponse {
  session_id: string
  parent_session_id?: string
  info?: SessionRuntimeInfo
}

/** `session.cwd.set` — change the session's working directory. */
export interface SessionCwdSetParams {
  session_id: string
  cwd: string
}

export interface SessionCwdSetResponse {
  cwd?: string
}

/* ---- blocking-request resolvers ---- */

export interface ClarifyRespondParams {
  request_id: string
  answer: string
  session_id?: string
}

export interface ApprovalRespondParams {
  request_id: string
  choice: string
  all?: boolean
  session_id?: string
}

export interface SudoRespondParams {
  request_id: string
  password: string
  session_id?: string
}

export interface SecretRespondParams {
  request_id: string
  value: string
  session_id?: string
}

export interface RespondResponse {
  status?: 'ok' | 'expired'
  resolved?: boolean
}

/**
 * The method table. `GatewayMethods[M]` is `{ params, response }`; the request
 * envelope that carries them is a standard JSON-RPC frame (id + method +
 * params), and the result is unwrapped from the frame's `result` field.
 */
export interface GatewayMethods {
  'session.create': { params: SessionCreateParams; response: SessionCreateResponse }
  'session.resume': { params: SessionResumeParams; response: SessionResumeResponse }
  'session.list': { params: SessionListParams; response: SessionListResponse }
  'session.most_recent': { params: SessionMostRecentParams; response: SessionMostRecentResponse }
  'session.activate': { params: SessionActivateParams; response: SessionActivateResponse }
  'session.redirect': { params: SessionRedirectParams; response: SessionRedirectResponse }
  'session.interrupt': { params: SessionInterruptParams; response: SessionInterruptResponse }
  'session.title': { params: SessionTitleParams; response: SessionTitleResponse }
  'session.usage': { params: SessionUsageParams; response: SessionUsageResponse }
  'session.status': { params: SessionStatusParams; response: SessionStatusResponse }
  'session.cwd.set': { params: SessionCwdSetParams; response: SessionCwdSetResponse }
  'session.branch': { params: SessionBranchParams; response: SessionBranchResponse }
  'session.delete': { params: SessionDeleteParams; response: SessionDeleteResponse }
  'prompt.submit': { params: PromptSubmitParams; response: PromptSubmitResponse }
  'clarify.respond': { params: ClarifyRespondParams; response: RespondResponse }
  'approval.respond': { params: ApprovalRespondParams; response: RespondResponse }
  'sudo.respond': { params: SudoRespondParams; response: RespondResponse }
  'secret.respond': { params: SecretRespondParams; response: RespondResponse }
}

/** Params type for a gateway method. */
export type GatewayParamsFor<M extends keyof GatewayMethods> = GatewayMethods[M]['params']
/** Response type for a gateway method. */
export type GatewayResponseFor<M extends keyof GatewayMethods> = GatewayMethods[M]['response']
