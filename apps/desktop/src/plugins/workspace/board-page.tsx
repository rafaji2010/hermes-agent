/**
 * Board Page — the kanban EXECUTION board as a workspace pane (M11.4).
 *
 * Renders the ADR-010 execution layer (upstream `hermes kanban`) as a
 * column board: todo → ready → running → review → done, cards with
 * assignee/priority, live counts, and a 15s self-refresh mirroring the
 * tasks page. Read-only by design — mutations happen through the CLI,
 * the agent tools, or the web dashboard's /kanban tab; this pane is the
 * glanceable desktop view.
 */

import {
  Button,
  cn,
  EmptyState,
  ErrorState,
  Loader,
  type PluginContext,
  useQuery,
  useQueryClient,
} from '@hermes/plugin-sdk'

import { fetchExecutionBoard, type KanbanBoardColumn, type KanbanTask } from './board-api'

const STATUS_COLORS: Record<string, string> = {
  todo: 'bg-gray-500/15 text-gray-700 border-gray-500/30 dark:bg-gray-400/20 dark:text-gray-100 dark:border-gray-400/40',
  ready: 'bg-cyan-500/15 text-cyan-700 border-cyan-500/30 dark:bg-cyan-400/20 dark:text-cyan-100 dark:border-cyan-400/40',
  running: 'bg-blue-500/15 text-blue-700 border-blue-500/30 dark:bg-blue-400/20 dark:text-blue-100 dark:border-blue-400/40',
  review: 'bg-purple-500/15 text-purple-700 border-purple-500/30 dark:bg-purple-400/20 dark:text-purple-100 dark:border-purple-400/40',
  done: 'bg-green-500/15 text-green-700 border-green-500/30 dark:bg-green-400/20 dark:text-green-100 dark:border-green-400/40',
  blocked: 'bg-red-500/15 text-red-700 border-red-500/30 dark:bg-red-400/20 dark:text-red-100 dark:border-red-400/40',
  scheduled: 'bg-orange-500/15 text-orange-700 border-orange-500/30 dark:bg-orange-400/20 dark:text-orange-100 dark:border-orange-400/40',
  archived: 'bg-gray-500/15 text-gray-600 border-gray-500/30 dark:bg-gray-400/20 dark:text-gray-300 dark:border-gray-400/40',
}

function statusColor(status: string): string {
  return STATUS_COLORS[status] || STATUS_COLORS.todo
}

function TaskCard({ task }: { task: KanbanTask }) {
  return (
    <div className="rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) p-2.5 space-y-1.5">
      <div className="text-xs font-medium text-(--ui-text-primary) leading-snug">{task.title}</div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          className={cn(
            'inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium',
            statusColor(task.status),
          )}
        >
          {task.status}
        </span>
        {task.priority && task.priority !== 'medium' && (
          <span className="text-[10px] text-(--ui-text-tertiary)">{task.priority}</span>
        )}
        {task.assignee && (
          <span className="text-[10px] text-(--ui-text-tertiary)">@{task.assignee}</span>
        )}
      </div>
      <div className="text-[10px] text-(--ui-text-tertiary) font-mono">{task.id}</div>
    </div>
  )
}

function BoardColumn({ column }: { column: KanbanBoardColumn }) {
  return (
    <div className="flex w-64 shrink-0 flex-col rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)">
      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary)">
          {column.name}
        </span>
        <span className="text-xs text-(--ui-text-tertiary)">{column.tasks.length}</span>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-2">
        {column.tasks.map(task => (
          <TaskCard key={task.id} task={task} />
        ))}
      </div>
    </div>
  )
}

export function BoardPage({ ctx }: { ctx: PluginContext }) {
  const queryClient = useQueryClient()

  const boardQuery = useQuery({
    queryKey: ['workspace', 'execution-board'],
    queryFn: () => fetchExecutionBoard(ctx),
    staleTime: 0,
    refetchInterval: 15_000,
  })

  const board = boardQuery.data

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-6 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-(--ui-text-primary)">Execution board</span>
          <span className="text-[10px] text-(--ui-text-tertiary)">
            hermes kanban · ADR-010 execution layer
          </span>
        </div>
        <div className="flex items-center gap-2">
          {board && (
            <span className="text-[10px] text-(--ui-text-tertiary)">
              event #{board.latest_event_id}
            </span>
          )}
          <Button
            disabled={boardQuery.isFetching}
            onClick={() => void queryClient.invalidateQueries({ queryKey: ['workspace', 'execution-board'] })}
            size="xs"
            variant="secondary"
          >
            Refresh
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-x-auto p-4">
        {boardQuery.isLoading && !board ? (
          <div className="flex justify-center py-12"><Loader type="lemniscate-bloom" /></div>
        ) : boardQuery.isError && !board ? (
          <ErrorState description="Failed to load the execution board." title="Error">
            <Button
              onClick={() => void queryClient.invalidateQueries({ queryKey: ['workspace', 'execution-board'] })}
              size="sm"
              variant="outline"
            >
              Retry
            </Button>
          </ErrorState>
        ) : !board || board.columns.length === 0 ? (
          <EmptyState
            description="No board yet — create cards with `hermes kanban create`."
            title="Empty execution board"
          />
        ) : (
          <div className="flex h-full gap-3">
            {board.columns.map(column => (
              <BoardColumn key={column.name} column={column} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
