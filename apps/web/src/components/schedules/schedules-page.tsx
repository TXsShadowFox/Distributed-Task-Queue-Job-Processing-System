"use client"

import { CalendarClock, Clock, MoreHorizontal, Pencil, Plus, Timer, Trash2, X } from "lucide-react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, GlassCard, PageHeader, RelativeTime, RowsSkeleton } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { ScheduleDialog } from "@/components/schedules/schedule-dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { errorMessage } from "@/lib/api/client"
import { useCancelTask, useDeleteSchedule, useSchedules, useTasks, useUpdateSchedule } from "@/lib/api/hooks"
import type { Schedule } from "@/lib/api/types"
import { dateTime } from "@/lib/format"
import { useCan } from "@/lib/session"
import { useUi } from "@/lib/ui-store"
import { cn } from "@/lib/utils"

function PauseSwitch({ schedule }: { schedule: Schedule }) {
  const update = useUpdateSchedule()
  const can = useCan()
  const active = schedule.status === "ACTIVE"
  return (
    <Switch
      checked={active}
      disabled={!can.write || update.isPending}
      aria-label={active ? `Pause ${schedule.name}` : `Resume ${schedule.name}`}
      onCheckedChange={(on) =>
        update.mutate(
          { id: schedule.id, patch: { paused: !on } },
          {
            onSuccess: () => toast.success(on ? `Resumed “${schedule.name}”` : `Paused “${schedule.name}”`),
            onError: (e) => toast.error(errorMessage(e)),
          },
        )
      }
    />
  )
}

function SchedulesTable({ onEdit, onDelete }: { onEdit: (s: Schedule) => void; onDelete: (s: Schedule) => void }) {
  const { data, isPending, isError, error, refetch } = useSchedules()
  const can = useCan()
  const highlight = useSearchParams().get("highlight")

  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (isPending) return <RowsSkeleton rows={4} />
  if (!data.length) {
    return (
      <EmptyState
        icon={<CalendarClock className="size-5" />}
        title="No schedules yet"
        description="Run a task type on a cron expression: nightly reports, hourly syncs, weekday reminders."
      />
    )
  }
  return (
    <div className="glass-strong overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Schedule</TableHead>
            <TableHead>When</TableHead>
            <TableHead>Task type</TableHead>
            <TableHead>Next run</TableHead>
            <TableHead>Last run</TableHead>
            <TableHead className="text-right">Runs</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="pr-4 text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((s) => (
            <TableRow key={s.id} className={cn(highlight === s.id && "bg-primary/10")} data-highlighted={highlight === s.id || undefined}>
              <TableCell className="pl-4">
                <p className="font-medium">{s.name}</p>
                {s.description && <p className="max-w-64 truncate text-xs text-muted-foreground">{s.description}</p>}
              </TableCell>
              <TableCell>
                <p className="font-mono text-xs">{s.cron_expression}</p>
                <p className="text-xs text-muted-foreground">
                  {s.cron_description} · {s.timezone}
                </p>
              </TableCell>
              <TableCell className="font-mono text-xs">{s.task_type}</TableCell>
              <TableCell>
                {s.status === "ACTIVE" && s.next_run_at ? (
                  <span title={dateTime(s.next_run_at, s.timezone)}>
                    <RelativeTime value={s.next_run_at} />
                  </span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell>
                {s.last_task_id ? (
                  <Link href={`/tasks/${s.last_task_id}`} className="text-primary hover:underline">
                    <RelativeTime value={s.last_run_at} />
                  </Link>
                ) : (
                  <span className="text-muted-foreground">Never</span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {s.total_runs}
                {s.skipped_runs > 0 && <span className="block text-xs text-muted-foreground">{s.skipped_runs} skipped</span>}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <PauseSwitch schedule={s} />
                  <StatusBadge status={s.status} />
                </div>
              </TableCell>
              <TableCell className="pr-4 text-right">
                <div className="flex items-center justify-end gap-1">
                  <Button variant="ghost" size="sm" asChild>
                    <Link href={`/tasks?schedule_id=${s.id}`} title="Tasks created by this schedule">
                      History
                    </Link>
                  </Button>
                  {can.write && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="size-8" aria-label={`More actions for ${s.name}`}>
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => onEdit(s)}>
                          <Pencil /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onSelect={() => onDelete(s)}>
                          <Trash2 /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function DelayedTasks() {
  const { data, isPending, isError, error, refetch } = useTasks({ state: ["SCHEDULED", "RETRY_SCHEDULED"], limit: 100 })
  const cancel = useCancelTask()
  const can = useCan()
  const openSubmit = useUi((s) => s.openSubmitTask)
  // The API sorts by creation time; "runs soonest" is the useful order here.
  const items = useMemo(
    () => [...(data?.items ?? [])].sort((a, b) => Date.parse(a.available_at ?? a.created_at) - Date.parse(b.available_at ?? b.created_at)),
    [data],
  )
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (isPending) return <RowsSkeleton rows={3} />
  if (!items.length) {
    return (
      <EmptyState
        icon={<Timer className="size-5" />}
        title="Nothing is waiting"
        description="Tasks submitted with a delay or a run-at time, and retries waiting out their backoff, appear here until they are due."
        action={
          can.write ? (
            <Button variant="outline" onClick={() => openSubmit()}>
              <Clock /> Submit a delayed task
            </Button>
          ) : undefined
        }
      />
    )
  }
  return (
    <div className="glass-strong overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Task</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Waiting for</TableHead>
            <TableHead>Runs</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="pr-4 text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((t) => (
            <TableRow key={t.id}>
              <TableCell className="pl-4">
                <Link href={`/tasks/${t.id}`} className="font-mono text-xs text-primary hover:underline" title={t.id}>
                  {t.id.slice(0, 8)}…{t.id.slice(-4)}
                </Link>
              </TableCell>
              <TableCell className="font-mono text-xs">{t.task_type}</TableCell>
              <TableCell>
                <StatusBadge status={t.state} />
              </TableCell>
              <TableCell>
                <span title={t.available_at ? dateTime(t.available_at) : undefined}>
                  <RelativeTime value={t.available_at} />
                </span>
              </TableCell>
              <TableCell className="text-muted-foreground">
                <RelativeTime value={t.created_at} />
              </TableCell>
              <TableCell className="pr-4 text-right">
                {can.write && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={cancel.isPending}
                    onClick={() =>
                      cancel.mutate(t.id, {
                        onSuccess: () => toast.success("Task cancelled"),
                        onError: (e) => toast.error(errorMessage(e)),
                      })
                    }
                  >
                    <X /> Cancel
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function SchedulesPage() {
  const can = useCan()
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<Schedule | null>(null)
  const remove = useDeleteSchedule()

  return (
    <div className="space-y-6">
      <PageHeader
        title="Schedules"
        description="Cron schedules with explicit time zones, plus one-off delayed tasks waiting to become due."
        actions={
          can.write && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> New schedule
            </Button>
          )
        }
      />

      <Tabs defaultValue="cron">
        <TabsList>
          <TabsTrigger value="cron">Cron schedules</TabsTrigger>
          <TabsTrigger value="delayed">Delayed tasks</TabsTrigger>
        </TabsList>
        <TabsContent value="cron" className="mt-3">
          <GlassCard className="overflow-hidden">
            <SchedulesTable onEdit={setEditing} onDelete={setDeleting} />
          </GlassCard>
        </TabsContent>
        <TabsContent value="delayed" className="mt-3">
          <GlassCard className="overflow-hidden">
            <DelayedTasks />
          </GlassCard>
        </TabsContent>
      </Tabs>

      <ScheduleDialog
        open={creating || !!editing}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false)
            setEditing(null)
          }
        }}
        existing={editing}
      />

      <AlertDialog open={!!deleting} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              It stops firing immediately. Tasks it already created are kept. To stop it temporarily, pause it instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() =>
                deleting &&
                remove.mutate(deleting.id, {
                  onSuccess: () => toast.success(`Deleted “${deleting.name}”`),
                  onError: (e) => toast.error(errorMessage(e)),
                })
              }
            >
              Delete schedule
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
