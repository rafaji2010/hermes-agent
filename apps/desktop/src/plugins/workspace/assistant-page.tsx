/**
 * Assistant Page — chat with suggested prompts, entity cards, analytics.
 *
 * U1C: conversation state re-homes with the effective workspace scope —
 * when the resolved workspace changes, messages/conversation/entities are
 * reset so one workspace's context never leaks into another.
 */

import {
  Button,
  cn,
  Contribute,
  Loader,
  type PluginContext,
  useQuery,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  $conversationId,
  $isThinking,
  $messages,
  $referencedEntities,
  type ChatMessage,
  type ReferencedEntity,
} from './assistant'
import { chat, getSuggestions } from './assistant-api'
import { scopeReady, useWorkspaceScope } from './scope'
import { WorkspaceScopeNotice } from './scope-notice'

const SUGGESTED_PROMPTS = [
  'What should I work on next?',
  'Show blocked tasks',
  'Summarize today\'s activity',
  'What ADRs are there?',
  'What changed this week?',
  'Which repository is most active?',
]

const TYPE_COLORS: Record<string, string> = {
  workspace: 'bg-blue-500/10 text-blue-400',
  task: 'bg-orange-500/10 text-orange-400',
  roadmap: 'bg-purple-500/10 text-purple-400',
  milestone: 'bg-yellow-500/10 text-yellow-400',
  adr: 'bg-green-500/10 text-green-400',
  journal: 'bg-pink-500/10 text-pink-400',
  repository: 'bg-cyan-500/10 text-cyan-400',
}

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function PageTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Assistant</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Entity card
// ---------------------------------------------------------------------------

function EntityCard({ entity }: { entity: ReferencedEntity }) {
  return (
    <div className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-xs space-y-1">
      <div className="flex items-center gap-2">
        <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-medium', TYPE_COLORS[entity.type] || 'bg-gray-500/10')}>
          {entity.type}
        </span>
        <span className="text-(--ui-text-primary) font-medium truncate">{entity.title}</span>
      </div>
      {entity.status && <span className="text-(--ui-text-tertiary)">Status: {entity.status}</span>}
      {entity.relevance && <span className="text-(--ui-text-tertiary) block text-[10px]">{entity.relevance}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

interface AssistantPageProps {
  ctx: PluginContext
}

export function AssistantPage({ ctx }: AssistantPageProps) {
  const [input, setInput] = useState('')
  const scope = useWorkspaceScope(ctx)
  const messagesEnd = useRef<HTMLDivElement>(null)

  // The resolved project scope is authoritative. An unresolvable scope
  // yields '' and gates the assistant off.
  const effectiveWs = scopeReady(scope) ? scope.workspaceId : ''

  const messages = useValue($messages)
  const convId = useValue($conversationId)
  const isThinking = useValue($isThinking)
  const refEntities = useValue($referencedEntities)

  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  // U1C re-home: conversation context is scoped to one effective workspace.
  // Whenever the resolved workspace changes (project/session/profile), reset
  // the chat so one workspace's context never leaks into another. The first
  // run is a no-op — state starts empty.
  useEffect(() => {
    $messages.set([])
    $conversationId.set('')
    $referencedEntities.set([])
  }, [effectiveWs])

  const suggestQ = useQuery({
    queryKey: ['workspace', 'suggestions', effectiveWs],
    queryFn: () => getSuggestions(ctx, effectiveWs),
    enabled: Boolean(effectiveWs),
    staleTime: 30000,
  })

  const handleSend = useCallback(async (text?: string) => {
    const q = (text || input).trim()

    if (!q || isThinking) {return}
    setInput('')
    const userMsg: ChatMessage = { role: 'user', content: q, referenced_entities: [] }
    $messages.set([...messages, userMsg])
    $isThinking.set(true)

    try {
      const r = await chat(ctx, q, convId, effectiveWs)
      const newConvId = r.conversation_id || convId

      if (newConvId && !convId) {$conversationId.set(newConvId)}
      $referencedEntities.set(r.referenced_entities)

      const asstMsg: ChatMessage = {
        role: 'assistant',
        content: r.answer,
        referenced_entities: r.referenced_entities,
      }

      $messages.set([...$messages.get(), asstMsg])
    } catch {
      const errMsg: ChatMessage = {
        role: 'assistant',
        content: 'Sorry, something went wrong. Please try again.',
        referenced_entities: [],
      }

      $messages.set([...$messages.get(), errMsg])
    } finally {
      $isThinking.set(false)
    }
  }, [input, messages, convId, isThinking, ctx, effectiveWs])

  const handleClear = useCallback(() => {
    $messages.set([])
    $conversationId.set('')
    $referencedEntities.set([])
  }, [])

  if (!effectiveWs) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace-assistant:titlebar"><PageTitlebar /></Contribute>
        <WorkspaceScopeNotice ctx={ctx} scope={scope} />
        <div className="flex flex-1 items-center justify-center px-8">
          <div className="text-center max-w-sm">
            <p className="text-sm text-(--ui-text-secondary) mb-4">No workspace scope resolved — the assistant is disabled (it never falls back to global).</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace-assistant:titlebar"><PageTitlebar /></Contribute>

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      <div className="flex flex-1 overflow-hidden">
        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 overflow-auto px-8 py-4 space-y-4">
            {messages.length === 0 && (
              <div className="text-center py-12">
                <p className="text-sm text-(--ui-text-secondary) mb-3">
                  Ask a question about your workspace.
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {SUGGESTED_PROMPTS.map((p, i) => (
                    <button
                      className="px-3 py-1.5 rounded border border-(--ui-stroke-tertiary) text-xs text-(--ui-text-secondary) hover:bg-(--ui-bg-secondary) hover:text-(--ui-text-primary)"
                      key={i}
                      onClick={() => void handleSend(p)}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div className={cn('flex gap-3', m.role === 'user' ? 'justify-end' : '')} key={i}>
                <div className={cn(
                  'max-w-[80%] rounded-lg p-4 text-sm',
                  m.role === 'user'
                    ? 'bg-(--ui-bg-primary) border border-(--ui-stroke-tertiary) text-(--ui-text-primary)'
                    : 'bg-(--ui-bg-secondary) border border-(--ui-stroke-tertiary) text-(--ui-text-secondary)',
                )}>
                  <div className="whitespace-pre-wrap">{m.content}</div>
                  {m.referenced_entities.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-(--ui-stroke-tertiary) space-y-1">
                      {m.referenced_entities.map(e => (
                        <EntityCard entity={e} key={`${e.type}:${e.id}`} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {isThinking && (
              <div className="flex gap-3">
                <div className="bg-(--ui-bg-secondary) border border-(--ui-stroke-tertiary) rounded-lg p-4">
                  <Loader type="lemniscate-bloom" />
                </div>
              </div>
            )}
            <div ref={messagesEnd} />
          </div>

          <div className="border-t border-(--ui-stroke-tertiary) px-8 py-3">
            <div className="flex gap-2">
              <input
                className="flex-1 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
                disabled={isThinking}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') {void handleSend()} }}
                placeholder="Ask about your workspace..."
                value={input}
              />
              <Button disabled={isThinking || !input.trim()} onClick={() => void handleSend()} size="sm">Send</Button>
              <Button onClick={handleClear} size="sm" variant="ghost">Clear</Button>
            </div>
          </div>
        </div>

        {/* Sidebar: entities + suggestions */}
        <div className="w-64 border-l border-(--ui-stroke-tertiary) p-4 space-y-4 overflow-auto shrink-0 hidden lg:block">
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary) mb-2">
              Referenced Entities
            </h4>
            {refEntities.length === 0 ? (
              <p className="text-xs text-(--ui-text-tertiary)">No entities yet.</p>
            ) : (
              <div className="space-y-1">{refEntities.map(e => <EntityCard entity={e} key={`${e.type}:${e.id}`} />)}</div>
            )}
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary) mb-2">
              Suggestions
            </h4>
            {suggestQ.data && suggestQ.data.suggestions.length > 0 && (
              <div className="space-y-2">
                {suggestQ.data.suggestions.map((s, i) => (
                  <button
                    className="w-full text-left rounded border border-(--ui-stroke-tertiary) px-3 py-2 text-xs hover:bg-(--ui-bg-secondary)"
                    key={i}
                    onClick={() => void handleSend(s.title.replace(/^Ask:\s*/, ''))}
                  >
                    <div className="font-medium text-(--ui-text-primary)">{s.title}</div>
                    <div className="text-(--ui-text-tertiary) mt-0.5">{s.description}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
