"use client"

import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  Bot,
  CalendarClock,
  ChevronDown,
  CircleDot,
  Cog,
  GitBranch,
  RotateCcw,
  SearchX,
  User,
  Workflow,
} from "lucide-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import {
  EmptyState,
  ErrorState,
  GlassCard,
  JsonView,
  KeyValue,
  MonoId,
  RelativeTime,
  RowsSkeleton,
} from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { TaskLogs } from "@/components/tasks/task-logs"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useCancelTask, useRetryTask, useTask, useTaskAttempts, useTaskEvents } from "@/lib/api/hooks"
import { CANCELLABLE_STATES, RETRYABLE_STATES, TERMINAL_STATES, type Attempt, type TaskEvent, type TaskOut } from "@/lib/api/types"
import { dateTime, duration } from "@/lib/format"
import { useCan } from "@/lib/session"
import { cn } from "@/lib/utils"

function TaskActions({ task }: { task: TaskOut }) {
  const can = useCan()
  const cancel = useCancelTask()
  const retry = useRetryTask()
  if (!can.write) return null
  const running = task.state === "RUNNING"
  return (
    <div className="flex flex-wrap gap-2">
      {RETRYABLE_STATES.has(task.state) && (
        <Button
          disabled={retry.isPending}
          onClick={() =>
            retry.mutate(task.id, {
              onSuccess: () => toast.success("Task re-queued", { description: "A fresh retry budget was granted." }),
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
        >
          <RotateCcw /> Retry
        </Button>
      )}
      {CANCELLABLE_STATES.has(task.state) && (
        <Confirm
          trigger={
            <Button variant="outline" disabled={cancel.isPending}>
              <Ban /> Cancel task
            </Button>
          }
          title="Cancel this task?"
          description={
            running
              ? "The worker is asked to stop the handler. Work it already did is not rolled back, so handlers should be idempotent."
              : "The task will not run. You can retry it later from this page."
          }
          confirmLabel="Cancel task"
          dismissLabel="Keep it"
          destructive
          onConfirm={() =>
            cancel.mutate(task.id, {
              onSuccess: (t) => toast.success(t.state === "CANCEL_REQUESTED" ? "Cancellation requested" : "Task cancelled"),
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
        />
      )}
    </div>
  )
}

function ErrorPanel({ task }: { task: TaskOut }) {
  const error = task.error as { type?: string; message?: string; kind?: string; retryable?: boolean; attempt?: number } | null
  if (!error) return null
  return (
    <GlassCard className="border-destructive/30 p-4" role="alert">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">
            <span className="font-mono">{error.type ?? "Error"}</span>
            {error.attempt != null && <span className="text-muted-foreground"> · attempt {error.attempt}</span>}
          </p>
          <p className="text-sm break-words whitespace-pre-wrap">{error.message}</p>
          <p className="text-xs text-muted-foreground">
            {error.kind === "timeout"
              ? "The attempt exceeded its timeout."
              : error.kind === "lost"
                ? "The worker stopped heartbeating, so the attempt's lease expired and it was reclaimed."
                : error.kind === "interrupted"
                  ? "The worker shut down before the attempt finished."
                  : error.retryable === false
                  ? "Non-retryable: the handler marked this failure as permanent."
                  : "Retryable failure."}
            {task.state === "DEAD_LETTERED" && " The task is in the dead-letter queue."}
          </p>
        </div>
      </div>
    </GlassCard>
  )
}

const EVENT_ICON: Record<string, typeof CircleDot> = { CREATED: CircleDot, RECONCILED: Cog, FENCED: AlertTriangle, ARCHIVED: Ban }

function Timeline({ taskId }: { taskId: string }) {
  const { data, isPending, isError, error, refetch } = useTaskEvents(taskId)
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (isPending) return <RowsSkeleton rows={4} />
  if (!data.length) return <EmptyState title="No events recorded" />
  return (
    <ol className="relative space-y-4 border-l border-border pl-6">
      {data.map((event: TaskEvent) => {
        const Icon = EVENT_ICON[event.event_type] ?? CircleDot
        const ActorIcon = event.actor_type === "worker" ? Bot : event.actor_type === "system" ? Cog : User
        return (
          <li key={event.id} className="relative">
            <span className="absolute top-0.5 -left-[33px] flex size-5 items-center justify-center rounded-full bg-background ring-1 ring-border">
              <Icon className="size-3 text-muted-foreground" aria-hidden />
            </span>
            <div className="flex flex-wrap items-center gap-2">
              {event.from_state && <StatusBadge status={event.from_state} />}
              {event.from_state && event.to_state && <span className="text-muted-foreground" aria-label="to">→</span>}
              {event.to_state && event.to_state !== event.from_state && <StatusBadge status={event.to_state} />}
              {!event.to_state && <span className="text-sm font-medium">{event.event_type}</span>}
              <RelativeTime value={event.created_at} className="text-xs text-muted-foreground" />
            </div>
            {event.message && <p className="mt-1 text-sm break-words">{event.message}</p>}
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
              <ActorIcon className="size-3" aria-hidden />
              {event.actor_type}
              {event.attempt_number != null && ` · attempt ${event.attempt_number}`}
              {` · ${dateTime(event.created_at)}`}
            </p>
          </li>
        )
      })}
    </ol>
  )
}

function AttemptRow({ attempt }: { attempt: Attempt }) {
  const [open, setOpen] = useState(false)
  return (
    <>
        <TableRow>
          <TableCell className="pl-4 tabular-nums">#{attempt.attempt_number}</TableCell>
          <TableCell>
            <StatusBadge status={attempt.status} />
          </TableCell>
          <TableCell className="text-xs text-muted-foreground">{attempt.worker_name ?? "—"}</TableCell>
          <TableCell>
            <RelativeTime value={attempt.started_at} className="text-muted-foreground" />
          </TableCell>
          <TableCell className="text-right tabular-nums">{duration(attempt.queue_wait_ms)}</TableCell>
          <TableCell className="text-right tabular-nums">{duration(attempt.duration_ms)}</TableCell>
          <TableCell className="max-w-72 truncate text-xs" title={attempt.error_message ?? undefined}>
            {attempt.error_type ? (
              <>
                <span className="font-mono text-destructive">{attempt.error_type}</span>
                {attempt.error_message && <span className="text-muted-foreground">: {attempt.error_message}</span>}
              </>
            ) : (
              "—"
            )}
          </TableCell>
          <TableCell className="pr-4 text-right">
            {attempt.traceback && (
              <Button
                variant="ghost"
                size="sm"
                aria-expanded={open}
                aria-controls={`traceback-${attempt.id}`}
                onClick={() => setOpen((v) => !v)}
              >
                Traceback <ChevronDown className={cn("transition-transform", open && "rotate-180")} />
              </Button>
            )}
          </TableCell>
        </TableRow>
        {attempt.traceback && open && (
          <TableRow id={`traceback-${attempt.id}`} className="hover:bg-transparent">
            <TableCell colSpan={8} className="px-4 pb-4">
              <pre className="glass-strong max-h-80 overflow-auto rounded-lg p-3 font-mono text-xs leading-relaxed whitespace-pre">
                {attempt.traceback}
              </pre>
            </TableCell>
          </TableRow>
        )}
    </>
  )
}

function Attempts({ taskId }: { taskId: string }) {
  const { data, isPending, isError, error, refetch } = useTaskAttempts(taskId)
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (isPending) return <RowsSkeleton rows={3} />
  if (!data.length) return <EmptyState title="No attempts yet" description="The task has not been picked up by a worker." />
  return (
    <div className="glass-strong overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Attempt</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Worker</TableHead>
            <TableHead>Started</TableHead>
            <TableHead className="text-right">Queue wait</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead>Error</TableHead>
            <TableHead className="pr-4">
              <span className="sr-only">Details</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((attempt) => (
            <AttemptRow key={attempt.id} attempt={attempt} />
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function Summary({ task }: { task: TaskOut }) {
  return (
    <GlassCard className="p-5">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-4">
        <KeyValue label="Queue">{task.queue}</KeyValue>
        <KeyValue label="Priority">
          <span className="tabular-nums">{task.priority}</span>
        </KeyValue>
        <KeyValue label="Attempts">
          <span className="tabular-nums">{task.attempt_count}</span>
        </KeyValue>
        <KeyValue label="Retries used">
          <span className="tabular-nums">
            {task.retry_count} of {task.max_retries}
          </span>
        </KeyValue>
        <KeyValue label="Created">
          <span title={dateTime(task.created_at)}>{dateTime(task.created_at)}</span>
        </KeyValue>
        <KeyValue label="Started">{task.started_at ? dateTime(task.started_at) : "—"}</KeyValue>
        <KeyValue label="Finished">{task.finished_at ? dateTime(task.finished_at) : "—"}</KeyValue>
        <KeyValue label="Duration">{duration(task.duration_ms)}</KeyValue>
        <KeyValue label="Worker">{task.worker_name ?? "—"}</KeyValue>
        <KeyValue label="Timeout">{duration(task.timeout_seconds * 1000)}</KeyValue>
        <KeyValue label="Backoff">
          {task.retry_backoff_seconds}s base, {task.retry_backoff_max_seconds}s max
        </KeyValue>
        {task.available_at && task.state !== "SUCCEEDED" && (
          <KeyValue label={task.state === "RETRY_SCHEDULED" ? "Next attempt" : "Runs at"}>
            <RelativeTime value={task.available_at} />
          </KeyValue>
        )}
        {task.idempotency_key && (
          <KeyValue label="Idempotency key">
            <span className="font-mono text-xs" title={task.idempotency_key}>
              {task.idempotency_key}
            </span>
          </KeyValue>
        )}
        {task.workflow_run_id && (
          <KeyValue label="Workflow run">
            <Link href={`/workflows/runs/${task.workflow_run_id}`} className="inline-flex items-center gap-1 text-primary hover:underline">
              <Workflow className="size-3.5" aria-hidden /> {task.workflow_run_id.slice(0, 8)}…
            </Link>
          </KeyValue>
        )}
        {task.schedule_id && (
          <KeyValue label="Schedule">
            <Link href={`/schedules?highlight=${task.schedule_id}`} className="inline-flex items-center gap-1 text-primary hover:underline">
              <CalendarClock className="size-3.5" aria-hidden /> {task.schedule_id.slice(0, 8)}…
            </Link>
          </KeyValue>
        )}
        {task.trace_id && (
          <KeyValue label="Trace ID">
            <span className="inline-flex items-center gap-1 font-mono text-xs">
              <GitBranch className="size-3.5" aria-hidden /> {task.trace_id}
            </span>
          </KeyValue>
        )}
      </dl>
    </GlassCard>
  )
}

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const { data: task, isPending, isError, error, refetch } = useTask(taskId)
  const [tab, setTab] = useState("timeline")

  if (isError) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
      return (
        <EmptyState
          className="mt-16"
          icon={<SearchX className="size-5" />}
          title="Task not found"
          description="It may belong to another project, or the ID is wrong."
          action={
            <Button asChild variant="outline">
              <Link href="/tasks">Back to tasks</Link>
            </Button>
          }
        />
      )
    }
    return <ErrorState error={error} onRetry={() => void refetch()} />
  }

  return (
    <div className="space-y-5">
      <Link href="/tasks" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Tasks
      </Link>

      {isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 space-y-1.5">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="font-mono text-xl font-semibold tracking-tight sm:text-2xl">{task.task_type}</h1>
                <StatusBadge status={task.state} />
              </div>
              <MonoId value={task.id} full className="text-muted-foreground" />
            </div>
            <TaskActions task={task} />
          </header>

          {(task.state === "RUNNING" || task.progress > 0) && !TERMINAL_STATES.has(task.state) && (
            <GlassCard className="space-y-2 p-4">
              <div className="flex items-center justify-between text-sm">
                <span>{task.progress_message ?? "Running…"}</span>
                <span className="tabular-nums text-muted-foreground">{Math.round(task.progress)}%</span>
              </div>
              <Progress value={task.progress} aria-label="Task progress" />
            </GlassCard>
          )}

          <ErrorPanel task={task} />
          <Summary task={task} />

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="flex-wrap">
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
              <TabsTrigger value="attempts">Attempts · {task.attempt_count}</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
              <TabsTrigger value="payload">Payload &amp; result</TabsTrigger>
              <TabsTrigger value="metadata">Metadata</TabsTrigger>
            </TabsList>
            <GlassCard className="mt-3 p-4 sm:p-5">
              <TabsContent value="timeline">
                <Timeline taskId={task.id} />
              </TabsContent>
              <TabsContent value="attempts">
                <Attempts taskId={task.id} />
              </TabsContent>
              <TabsContent value="logs">
                <TaskLogs taskId={task.id} live={!TERMINAL_STATES.has(task.state)} />
              </TabsContent>
              <TabsContent value="payload" className="grid gap-4 lg:grid-cols-2">
                <section className="space-y-2">
                  <h2 className="text-sm font-medium">Payload</h2>
                  <JsonView value={task.payload} />
                </section>
                <section className="space-y-2">
                  <h2 className="text-sm font-medium">Result</h2>
                  {task.state === "SUCCEEDED" ? (
                    <JsonView value={task.result ?? null} />
                  ) : (
                    <p className="text-sm text-muted-foreground">Available once the task succeeds.</p>
                  )}
                </section>
              </TabsContent>
              <TabsContent value="metadata">
                {Object.keys(task.metadata).length ? (
                  <JsonView value={task.metadata} />
                ) : (
                  <p className="text-sm text-muted-foreground">No metadata was attached to this task.</p>
                )}
              </TabsContent>
            </GlassCard>
          </Tabs>
        </>
      )}
    </div>
  )
}
