"use client"

import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Filter, ListChecks, PlusCircle, Search, X } from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"

import { EmptyState, ErrorState, GlassCard, PageHeader, RelativeTime, RowsSkeleton } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useTasks, useTaskTypes, type TaskFilters } from "@/lib/api/hooks"
import { TASK_STATES } from "@/lib/api/types"
import { duration, titleCase } from "@/lib/format"
import { useCan } from "@/lib/session"
import { useUi } from "@/lib/ui-store"
import { replaceQuery } from "@/lib/url-state"
import { cn } from "@/lib/utils"

type SortField = NonNullable<TaskFilters["sort"]>
const PAGE_SIZES = [25, 50, 100]
const ALL = "__all__"

function useUrlState() {
  const params = useSearchParams()
  const pathname = usePathname()
  const set = (updates: Record<string, string | string[] | null>) => {
    const next = new URLSearchParams(params.toString())
    for (const [key, value] of Object.entries(updates)) {
      next.delete(key)
      if (Array.isArray(value)) value.forEach((v) => next.append(key, v))
      else if (value) next.set(key, value)
    }
    replaceQuery(pathname, next)
  }
  return { params, set }
}

function SortHeader({
  field,
  label,
  sort,
  order,
  onSort,
  className,
}: {
  field: SortField
  label: string
  sort: SortField
  order: "asc" | "desc"
  onSort: (field: SortField) => void
  className?: string
}) {
  const active = sort === field
  const Icon = active ? (order === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown
  return (
    <TableHead className={className} aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={cn("inline-flex items-center gap-1 rounded hover:text-foreground", active && "text-foreground")}
      >
        {label}
        <Icon className="size-3.5" aria-hidden />
      </button>
    </TableHead>
  )
}

function FilterChip({ label, value, onClear }: { label: string; value: string; onClear: () => void }) {
  return (
    <span className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-muted/40 pr-1 pl-3 text-xs">
      {label}
      <span className="font-mono text-muted-foreground" title={value}>
        {value.slice(0, 8)}…
      </span>
      <Button variant="ghost" size="icon" className="size-6" onClick={onClear} aria-label={`Remove ${label.toLowerCase()} filter`}>
        <X />
      </Button>
    </span>
  )
}

export function TasksPage() {
  const { params, set } = useUrlState()
  const { data: taskTypes } = useTaskTypes()
  const openSubmit = useUi((s) => s.openSubmitTask)
  const can = useCan()
  const router = useRouter()

  const q = params.get("q") ?? ""
  const states = params.getAll("state")
  const taskType = params.get("task_type") ?? ""
  const queue = params.get("queue") ?? ""
  const scheduleId = params.get("schedule_id") ?? ""
  const runId = params.get("workflow_run_id") ?? ""
  const sort = (params.get("sort") as SortField | null) ?? "created_at"
  const order = (params.get("order") as "asc" | "desc" | null) ?? "desc"
  const limit = Number(params.get("limit") ?? 25) || 25
  const filterKey = [q, states.join(","), taskType, queue, scheduleId, runId, sort, order, limit].join("|")

  // Cursor stack for Previous/Next; reset whenever the filters change.
  const [cursors, setCursors] = useState<{ key: string; stack: (string | null)[] }>({ key: filterKey, stack: [null] })
  const stack = cursors.key === filterKey ? cursors.stack : [null]
  const cursor = stack[stack.length - 1]
  const [search, setSearch] = useState({ key: q, value: q })
  const searchValue = search.key === q ? search.value : q

  const filters: TaskFilters = useMemo(
    () => ({
      q: q || undefined,
      state: states.length ? states : undefined,
      task_type: taskType || undefined,
      queue: queue || undefined,
      schedule_id: scheduleId || undefined,
      workflow_run_id: runId || undefined,
      sort,
      order,
      limit,
      cursor,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filterKey, cursor],
  )
  const { data, isPending, isError, error, refetch, isFetching } = useTasks(filters)
  const queues = useMemo(() => [...new Set((taskTypes ?? []).map((t) => t.queue))].sort(), [taskTypes])
  const filtered = !!(q || states.length || taskType || queue || scheduleId || runId)

  const onSort = (field: SortField) =>
    set({ sort: field, order: sort === field && order === "desc" ? "asc" : "desc" })

  return (
    <div className="space-y-5">
      <PageHeader
        title="Tasks"
        description="Every task in this project. Filters, sorting and pagination live in the URL, so views are shareable."
        actions={
          can.write && (
            <Button onClick={() => openSubmit()}>
              <PlusCircle /> Submit task
            </Button>
          )
        }
      />

      <GlassCard className="flex flex-wrap items-end gap-3 p-3">
        <form
          className="relative min-w-56 flex-1"
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            set({ q: searchValue.trim() || null })
          }}
        >
          <Label htmlFor="task-search" className="sr-only">
            Search tasks
          </Label>
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            id="task-search"
            placeholder="Task ID, ID prefix, idempotency key or type…  (Enter)"
            value={searchValue}
            onChange={(e) => setSearch({ key: q, value: e.target.value })}
            onBlur={() => searchValue.trim() !== q && set({ q: searchValue.trim() || null })}
            className="h-9 pl-9 font-mono text-xs"
          />
        </form>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="h-9">
              <Filter /> State{states.length ? ` · ${states.length}` : ""}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-60" align="start">
            <fieldset className="space-y-2">
              <legend className="mb-2 text-xs font-medium text-muted-foreground">Show tasks in state</legend>
              {TASK_STATES.map((s) => (
                <Label key={s} className="flex items-center gap-2 text-sm font-normal">
                  <Checkbox
                    checked={states.includes(s)}
                    onCheckedChange={(checked) =>
                      set({ state: checked ? [...states, s] : states.filter((x) => x !== s) })
                    }
                  />
                  {titleCase(s)}
                </Label>
              ))}
            </fieldset>
          </PopoverContent>
        </Popover>

        <Select value={taskType || ALL} onValueChange={(v) => set({ task_type: v === ALL ? null : v })}>
          <SelectTrigger className="h-9 w-48" aria-label="Task type">
            <SelectValue placeholder="All task types" />
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

        <Select value={queue || ALL} onValueChange={(v) => set({ queue: v === ALL ? null : v })}>
          <SelectTrigger className="h-9 w-36" aria-label="Queue">
            <SelectValue placeholder="All queues" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All queues</SelectItem>
            {queues.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {scheduleId && <FilterChip label="Schedule" value={scheduleId} onClear={() => set({ schedule_id: null })} />}
        {runId && <FilterChip label="Workflow run" value={runId} onClear={() => set({ workflow_run_id: null })} />}
        {filtered && (
          <Button
            variant="ghost"
            className="h-9"
            onClick={() => set({ q: null, state: null, task_type: null, queue: null, schedule_id: null, workflow_run_id: null })}
          >
            <X /> Clear
          </Button>
        )}
      </GlassCard>

      <GlassCard className="overflow-hidden">
        {isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : isPending ? (
          <RowsSkeleton rows={8} />
        ) : !data.items.length ? (
          <EmptyState
            icon={<ListChecks className="size-5" />}
            title={filtered ? "No tasks match these filters" : "No tasks yet"}
            description={filtered ? "Try widening the filters or clearing the search." : "Submit a task from the dashboard, the API or the Python SDK."}
            action={
              !filtered && can.write ? (
                <Button onClick={() => openSubmit()}>
                  <PlusCircle /> Submit your first task
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className={cn("glass-strong overflow-x-auto transition-opacity", isFetching && "opacity-70")}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">ID</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                  <SortHeader field="priority" label="Priority" sort={sort} order={order} onSort={onSort} />
                  <TableHead>Queue</TableHead>
                  <SortHeader field="created_at" label="Created" sort={sort} order={order} onSort={onSort} />
                  <SortHeader field="updated_at" label="Updated" sort={sort} order={order} onSort={onSort} />
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead>Worker</TableHead>
                  <TableHead className="pr-4 text-right">Retries</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((t) => (
                  <TableRow
                    key={t.id}
                    className="cursor-pointer"
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest("a,button")) return
                      router.push(`/tasks/${t.id}`)
                    }}
                  >
                    <TableCell className="pl-4">
                      <Link href={`/tasks/${t.id}`} title={t.id} className="font-mono text-xs text-primary underline-offset-2 hover:underline">
                        {t.id.slice(0, 8)}…{t.id.slice(-4)}
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{t.task_type}</TableCell>
                    <TableCell>
                      <StatusBadge status={t.state} />
                    </TableCell>
                    <TableCell className="tabular-nums">{t.priority}</TableCell>
                    <TableCell className="text-muted-foreground">{t.queue}</TableCell>
                    <TableCell className="text-muted-foreground">
                      <RelativeTime value={t.created_at} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <RelativeTime value={t.updated_at} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{duration(t.duration_ms)}</TableCell>
                    <TableCell className="max-w-40 truncate text-xs text-muted-foreground">{t.worker_name ?? "—"}</TableCell>
                    <TableCell className="pr-4 text-right tabular-nums">
                      {t.retry_count}/{t.max_retries}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {data && data.items.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm">
            <p className="text-muted-foreground">
              Page {stack.length} · {data.items.length} of {data.total ?? "?"} matching tasks
            </p>
            <div className="flex items-center gap-2">
              <Select value={String(limit)} onValueChange={(v) => set({ limit: v === "25" ? null : v })}>
                <SelectTrigger className="h-8 w-32" aria-label="Rows per page">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      {n} / page
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="sm"
                disabled={stack.length <= 1}
                onClick={() => setCursors({ key: filterKey, stack: stack.slice(0, -1) })}
              >
                <ChevronLeft /> Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!data.has_more}
                onClick={() => setCursors({ key: filterKey, stack: [...stack, data.next_cursor ?? null] })}
              >
                Next <ChevronRight />
              </Button>
            </div>
          </div>
        )}
      </GlassCard>
    </div>
  )
}

