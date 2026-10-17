"use client"

import { Archive, ChevronLeft, ChevronRight, Inbox, Loader2, RotateCcw, ScanSearch } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import { EmptyState, ErrorState, GlassCard, JsonView, KeyValue, PageHeader, RelativeTime, RowsSkeleton } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { errorMessage } from "@/lib/api/client"
import { useArchiveTask, useBulkDeadLetter, useDeadLetter, useRetryTask, useTask, useTaskAttempts, useTaskTypes } from "@/lib/api/hooks"
import type { BulkResult } from "@/lib/api/types"
import { dateTime } from "@/lib/format"
import { useCan } from "@/lib/session"

const ALL = "__all__"

function reportBulk(result: BulkResult, verb: string) {
  if (!result.failed) {
    toast.success(`${result.succeeded} task${result.succeeded === 1 ? "" : "s"} ${verb}`)
    return
  }
  const reasons = [...new Set(result.results.filter((r) => !r.ok).map((r) => r.error ?? "unknown error"))]
  toast.warning(`${result.succeeded} ${verb}, ${result.failed} skipped`, { description: reasons.slice(0, 3).join("; ") })
}

function InspectSheet({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const task = useTask(taskId ?? "")
  const attempts = useTaskAttempts(taskId ?? "")
  const retry = useRetryTask()
  const archive = useArchiveTask()
  const can = useCan()
  const t = task.data
  const error = t?.error as { type?: string; message?: string; kind?: string } | null | undefined
  const lastAttempt = attempts.data?.at(-1)

  return (
    <Sheet open={!!taskId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="font-mono">{t?.task_type ?? "Task"}</SheetTitle>
          <SheetDescription className="font-mono text-xs">{taskId}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6">
          {task.isError ? (
            <ErrorState error={task.error} onRetry={() => void task.refetch()} />
          ) : !t ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={t.state} />
                {t.archived_at && <span className="text-xs text-muted-foreground">Archived {dateTime(t.archived_at)}</span>}
                <Button variant="link" size="sm" asChild className="ml-auto">
                  <Link href={`/tasks/${t.id}`}>Open full task</Link>
                </Button>
              </div>
              {error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3" role="alert">
                  <p className="font-mono text-sm font-medium">{error.type ?? "Error"}</p>
                  <p className="mt-1 text-sm break-words whitespace-pre-wrap">{error.message}</p>
                </div>
              )}
              <dl className="grid grid-cols-2 gap-4">
                <KeyValue label="Dead-lettered">{t.dead_lettered_at ? dateTime(t.dead_lettered_at) : "—"}</KeyValue>
                <KeyValue label="Attempts">{t.attempt_count}</KeyValue>
                <KeyValue label="Retries used">
                  {t.retry_count} of {t.max_retries}
                </KeyValue>
                <KeyValue label="Queue">{t.queue}</KeyValue>
              </dl>
              {lastAttempt?.traceback && (
                <section className="space-y-2">
                  <h3 className="text-sm font-medium">Last traceback · attempt {lastAttempt.attempt_number}</h3>
                  <pre className="glass-strong max-h-64 overflow-auto rounded-lg p-3 font-mono text-xs leading-relaxed">{lastAttempt.traceback}</pre>
                </section>
              )}
              <section className="space-y-2">
                <h3 className="text-sm font-medium">Payload</h3>
                <JsonView value={t.payload} maxHeight="14rem" />
              </section>
              {can.write && t.state === "DEAD_LETTERED" && (
                <div className="flex gap-2">
                  <Button
                    disabled={retry.isPending}
                    onClick={() =>
                      retry.mutate(t.id, {
                        onSuccess: () => (toast.success("Task re-queued"), onClose()),
                        onError: (e) => toast.error(errorMessage(e)),
                      })
                    }
                  >
                    <RotateCcw /> Retry
                  </Button>
                  {!t.archived_at && (
                    <Button
                      variant="outline"
                      disabled={archive.isPending}
                      onClick={() =>
                        archive.mutate(t.id, {
                          onSuccess: () => (toast.success("Task archived"), onClose()),
                          onError: (e) => toast.error(errorMessage(e)),
                        })
                      }
                    >
                      <Archive /> Archive
                    </Button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export function DeadLetterPage() {
  const can = useCan()
  const { data: taskTypes } = useTaskTypes()
  const [taskType, setTaskType] = useState(ALL)
  const [includeArchived, setIncludeArchived] = useState(false)
  const filterKey = `${taskType}|${includeArchived}`
  const [pages, setPages] = useState<{ key: string; stack: (string | null)[] }>({ key: filterKey, stack: [null] })
  const stack = pages.key === filterKey ? pages.stack : [null]
  const [selection, setSelection] = useState<{ key: string; ids: Set<string> }>({ key: filterKey, ids: new Set() })
  const selected = selection.key === filterKey ? selection.ids : new Set<string>()
  const [inspecting, setInspecting] = useState<string | null>(null)

  const { data, isPending, isError, error, refetch, isFetching } = useDeadLetter({
    task_type: taskType === ALL ? undefined : taskType,
    include_archived: includeArchived,
    cursor: stack[stack.length - 1],
  })
  const bulkRetry = useBulkDeadLetter("retry")
  const bulkArchive = useBulkDeadLetter("archive")

  const items = data?.items ?? []
  const selectable = items.filter((t) => !t.archived_at)
  const allSelected = selectable.length > 0 && selectable.every((t) => selected.has(t.id))
  const toggle = (id: string, on: boolean) => {
    const next = new Set(selected)
    if (on) next.add(id)
    else next.delete(id)
    setSelection({ key: filterKey, ids: next })
  }
  const clearSelection = () => setSelection({ key: filterKey, ids: new Set() })
  const ids = [...selected]

  return (
    <div className="space-y-5">
      <PageHeader
        title="Dead letter queue"
        description="Tasks that failed for good: their retries ran out, or the handler raised a non-retryable error. Inspect them, fix the cause, then retry with a fresh retry budget or archive them."
      />

      <GlassCard className="flex flex-wrap items-center gap-3 p-3">
        <Select value={taskType} onValueChange={setTaskType}>
          <SelectTrigger className="h-9 w-52" aria-label="Task type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All task types</SelectItem>
            {(taskTypes ?? []).map((t) => (
              <SelectItem key={t.name} value={t.name}>
                <span className="font-mono text-xs">{t.name}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Label className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
          <Switch checked={includeArchived} onCheckedChange={setIncludeArchived} /> Include archived
        </Label>
        {can.write && ids.length > 0 && (
          <div className="ml-auto flex flex-wrap items-center gap-2" role="toolbar" aria-label="Bulk actions">
            <span className="text-sm text-muted-foreground">{ids.length} selected</span>
            <Button
              size="sm"
              disabled={bulkRetry.isPending}
              onClick={() =>
                bulkRetry.mutate(ids, {
                  onSuccess: (r) => (reportBulk(r, "re-queued"), clearSelection()),
                  onError: (e) => toast.error(errorMessage(e)),
                })
              }
            >
              {bulkRetry.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />} Retry
            </Button>
            <Confirm
              trigger={
                <Button size="sm" variant="outline" disabled={bulkArchive.isPending}>
                  <Archive /> Archive
                </Button>
              }
              title={`Archive ${ids.length} task${ids.length === 1 ? "" : "s"}?`}
              description="Archived tasks leave the queue but keep their history; turn on “Include archived” to see them again."
              confirmLabel="Archive"
              onConfirm={() =>
                bulkArchive.mutate(ids, {
                  onSuccess: (r) => (reportBulk(r, "archived"), clearSelection()),
                  onError: (e) => toast.error(errorMessage(e)),
                })
              }
            />
            <Button size="sm" variant="ghost" onClick={clearSelection}>
              Clear
            </Button>
          </div>
        )}
      </GlassCard>

      <GlassCard className="overflow-hidden">
        {isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : isPending ? (
          <RowsSkeleton rows={5} />
        ) : !items.length ? (
          <EmptyState
            icon={<Inbox className="size-5" />}
            title="The dead letter queue is empty"
            description={taskType === ALL ? "Nothing has failed for good. Tasks land here after their last retry fails." : "No dead-lettered tasks of this type."}
          />
        ) : (
          <div className={isFetching ? "glass-strong overflow-x-auto opacity-70 transition-opacity" : "glass-strong overflow-x-auto transition-opacity"}>
            <Table>
              <TableHeader>
                <TableRow>
                  {can.write && (
                    <TableHead className="w-10 pl-4">
                      <Checkbox
                        checked={allSelected}
                        disabled={!selectable.length}
                        onCheckedChange={(on) =>
                          setSelection({ key: filterKey, ids: on ? new Set(selectable.map((t) => t.id)) : new Set() })
                        }
                        aria-label="Select every task on this page"
                      />
                    </TableHead>
                  )}
                  <TableHead className={can.write ? undefined : "pl-4"}>Task</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Error</TableHead>
                  <TableHead className="text-right">Attempts</TableHead>
                  <TableHead>Dead-lettered</TableHead>
                  <TableHead className="pr-4 text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((t) => (
                  <TableRow key={t.id} data-state={selected.has(t.id) ? "selected" : undefined}>
                    {can.write && (
                      <TableCell className="pl-4">
                        <Checkbox
                          checked={selected.has(t.id)}
                          disabled={!!t.archived_at}
                          onCheckedChange={(on) => toggle(t.id, on === true)}
                          aria-label={`Select task ${t.id}`}
                        />
                      </TableCell>
                    )}
                    <TableCell className={can.write ? undefined : "pl-4"}>
                      <Link href={`/tasks/${t.id}`} className="font-mono text-xs text-primary hover:underline" title={t.id}>
                        {t.id.slice(0, 8)}…{t.id.slice(-4)}
                      </Link>
                      {t.archived_at && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">archived</span>}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{t.task_type}</TableCell>
                    <TableCell className="font-mono text-xs text-destructive">{t.error_type ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{t.attempt_count}</TableCell>
                    <TableCell className="text-muted-foreground">
                      <RelativeTime value={t.finished_at ?? t.updated_at} />
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button variant="ghost" size="sm" onClick={() => setInspecting(t.id)}>
                        <ScanSearch /> Inspect
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {data && items.length > 0 && (stack.length > 1 || data.has_more) && (
          <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm">
            <span className="text-muted-foreground">Page {stack.length}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={stack.length <= 1} onClick={() => setPages({ key: filterKey, stack: stack.slice(0, -1) })}>
                <ChevronLeft /> Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!data.has_more}
                onClick={() => setPages({ key: filterKey, stack: [...stack, data.next_cursor ?? null] })}
              >
                Next <ChevronRight />
              </Button>
            </div>
          </div>
        )}
      </GlassCard>

      <InspectSheet taskId={inspecting} onClose={() => setInspecting(null)} />
    </div>
  )
}
