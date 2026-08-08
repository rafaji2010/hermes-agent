/**
 * Workspace Plugin — Shell & Internal Navigation (U1C)
 *
 * The Workspace plugin registers ONE contributed route (`/workspace`) —
 * the current upstream route contract defines one-segment contributed
 * paths.  All Workspace surfaces (overview, ADRs, journal, roadmaps,
 * tasks, search, analytics, assistant) live behind this root and switch
 * through internal navigation, keeping every surface reachable through
 * the SDK boundary.
 */

import { atom, cn, Codicon, type PluginContext, useValue } from '@hermes/plugin-sdk'

import { ADRPage } from './adr-page'
import { AnalyticsPage } from './analytics-page'
import { AssistantPage } from './assistant-page'
import { JournalPage } from './journal-page'
import { RoadmapsPage } from './roadmaps-page'
import { SearchPage } from './search-page'
import { TasksPage } from './tasks-page'
import { WorkspacePage } from './workspace-page'

export type WorkspaceTab =
  | 'overview'
  | 'adrs'
  | 'journal'
  | 'roadmaps'
  | 'tasks'
  | 'search'
  | 'analytics'
  | 'assistant'

export interface WorkspaceTabDef {
  id: WorkspaceTab
  label: string
  codicon: string
}

export const WORKSPACE_TABS: WorkspaceTabDef[] = [
  { id: 'overview', label: 'Overview', codicon: 'dashboard' },
  { id: 'adrs', label: 'ADRs', codicon: 'book' },
  { id: 'journal', label: 'Journal', codicon: 'note' },
  { id: 'roadmaps', label: 'Roadmaps', codicon: 'map' },
  { id: 'tasks', label: 'Tasks', codicon: 'checklist' },
  { id: 'search', label: 'Search', codicon: 'search' },
  { id: 'analytics', label: 'Analytics', codicon: 'graph-line' },
  { id: 'assistant', label: 'Assistant', codicon: 'comment-discussion' },
]

export const $workspaceTab = atom<WorkspaceTab>('overview')

// ---------------------------------------------------------------------------
// Tab bar — internal Workspace navigation (the SDK surface for the app's
// sidebar row; no contributed sub-routes are registered).
// ---------------------------------------------------------------------------

function WorkspaceTabBar() {
  const tab = useValue($workspaceTab)

  return (
    <div className="flex shrink-0 items-center gap-0.5 overflow-x-auto border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-3 py-1.5">
      {WORKSPACE_TABS.map(def => (
        <button
          className={cn(
            'inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors',
            tab === def.id
              ? 'bg-(--ui-bg-primary) text-(--ui-text-primary) shadow-none'
              : 'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
          )}
          key={def.id}
          onClick={() => $workspaceTab.set(def.id)}
        >
          <Codicon name={def.codicon} size="0.8125rem" />
          {def.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shell — mounts the active Workspace surface.
// ---------------------------------------------------------------------------

interface WorkspaceShellProps {
  ctx: PluginContext
}

export function WorkspaceShell({ ctx }: WorkspaceShellProps) {
  const tab = useValue($workspaceTab)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceTabBar />
      <div className="relative min-h-0 flex-1">
        {tab === 'overview' && <WorkspacePage ctx={ctx} />}
        {tab === 'adrs' && <ADRPage ctx={ctx} />}
        {tab === 'journal' && <JournalPage ctx={ctx} />}
        {tab === 'roadmaps' && <RoadmapsPage ctx={ctx} />}
        {tab === 'tasks' && <TasksPage ctx={ctx} />}
        {tab === 'search' && <SearchPage ctx={ctx} />}
        {tab === 'analytics' && <AnalyticsPage ctx={ctx} />}
        {tab === 'assistant' && <AssistantPage ctx={ctx} />}
      </div>
    </div>
  )
}
