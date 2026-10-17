"use client"

import {
  Activity,
  AlertOctagon,
  CheckCircle2,
  Clock,
  Cpu,
  Gauge,
  Inbox,
  Loader,
  PlusCircle,
  Timer,
  XCircle,
} from "lucide-react"
import Link from "next/link"
import { useMemo } from "react"

import { BarList, BarTable, ChartFrame, SeriesTable, TimeSeriesChart, timeFormatter, type SeriesSpec } from "@/components/charts/charts"
import { EmptyState, ErrorState, GlassCard, PageHeader, RelativeTime, SectionTitle, StatCard } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { useLatency, useOverview, useSystemStatus, useThroughput } from "@/lib/api/hooks"
import { compactNumber, duration, percent, titleCase } from "@/lib/format"
import { useRealtime, type ActivityItem } from "@/lib/realtime"
import { useCan } from "@/lib/session"
import { useUi } from "@/lib/ui-store"

const THROUGHPUT_SERIES: SeriesSpec[] = [
  { key: "succeeded", label: "Succeeded", slot: 1 },
  { key: "submitted", label: "Submitted", slot: 2 },
  { key: "failed", label: "Failed", slot: 3 },
]

const STATE_ORDER = [
  "QUEUED",
  "SCHEDULED",
  "RUNNING",
  "RETRY_SCHEDULED",
  "CANCEL_REQUESTED",
  "SUCCEEDED",
  "FAILED",
  "DEAD_LETTERED",
  "CANCELLED",
]

function ActivityRow({ item }: { item: ActivityItem }) {
  const data = item.data
  if (item.type === "task.updated" && !data.bulk) {
    return (
      <Link
        href={`/tasks/${String(data.id)}`}
        className="flex items-center gap-3 rounded-lg px-2 py-2 text-sm hover:bg-muted"
      >
        <StatusBadge status={String(data.state)} />
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{String(data.task_type)}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{String(data.id).slice(0, 8)}</span>
      </Link>
    )
  }
  const text =
    item.type === "task.updated"
      ? `${String(data.count)} tasks submitted`
      : item.type === "workflow_run.updated"
        ? `Workflow run ${String(data.id).slice(0, 8)} is ${titleCase(String(data.state))}`
        : item.type === "schedule.updated"
          ? `Schedule ${String(data.id).slice(0, 8)} updated`
          : item.type
  const href = item.type === "workflow_run.updated" ? `/workflows/runs/${String(data.id)}` : undefined
  const body = (
    <span className="flex items-center gap-3 rounded-lg px-2 py-2 text-sm">
      <Activity className="size-3.5 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{text}</span>
    </span>
  )
  return href ? (
    <Link href={href} className="block rounded-lg hover:bg-muted">
      {body}
    </Link>
  ) : (
    body
  )
}

export function OverviewPage() {
  const overview = useOverview()
  const throughput = useThroughput("1h")
  const latency = useLatency("1h")
  const system = useSystemStatus()
  const activity = useRealtime((s) => s.activity)
  const realtime = useRealtime((s) => s.status)
  const openSubmit = useUi((s) => s.openSubmitTask)
  const can = useCan()
  const o = overview.data

  const formatTime = useMemo(() => timeFormatter(throughput.data?.bucket_seconds ?? 60), [throughput.data?.bucket_seconds])
  const series = useMemo(() => (throughput.data?.series ?? []) as unknown as Record<string, number | string | null>[], [throughput.data])
  const stateBars = useMemo(
    () =>
      STATE_ORDER.filter((s) => (o?.tasks_by_state[s] ?? 0) > 0).map((s) => ({
        label: titleCase(s),
        value: o?.tasks_by_state[s] ?? 0,
      })),
    [o],
  )
  const queueBars = useMemo(
    () =>
      (o?.queue_depth ?? []).map((q) => ({
        label: q.queue,
        value: q.queued,
        hint: q.oldest_queued_at ? `oldest queued ${new Date(q.oldest_queued_at).toLocaleTimeString()}` : undefined,
      })),
    [o],
  )

  if (overview.isError) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Live state of this project — every number comes from the API."
        actions={
          can.write && (
            <Button onClick={() => openSubmit()}>
              <PlusCircle /> Submit task
            </Button>
          )
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <StatCard label="Running" value={compactNumber(o?.running)} icon={<Loader className="size-4" />} tone="info" loading={overview.isPending} hint={`${o?.workers.busy_slots ?? 0} of ${o?.workers.capacity ?? 0} worker slots busy`} />
        <StatCard label="Queued" value={compactNumber(o?.queued)} icon={<Inbox className="size-4" />} loading={overview.isPending} hint={`${compactNumber(o?.scheduled)} scheduled or retrying`} />
        <StatCard label="Succeeded (24h)" value={compactNumber(o?.succeeded_24h)} icon={<CheckCircle2 className="size-4" />} tone="success" loading={overview.isPending} hint={`Success rate ${percent(o?.success_rate_24h)}`} />
        <StatCard label="Failed (24h)" value={compactNumber(o?.failed_24h)} icon={<XCircle className="size-4" />} tone="danger" loading={overview.isPending} hint={`${compactNumber(o?.retries_24h)} retries`} />
        <StatCard label="Dead letter" value={compactNumber(o?.dead_letter_open)} icon={<AlertOctagon className="size-4" />} tone="warning" loading={overview.isPending} hint={<Link href="/dead-letter" className="underline-offset-2 hover:underline">Open queue</Link>} />
        <StatCard label="Throughput" value={`${(o?.throughput_per_min_1h ?? 0).toFixed(1)}/min`} icon={<Gauge className="size-4" />} loading={overview.isPending} hint="Succeeded per minute, last hour" />
        <StatCard label="Avg duration (24h)" value={duration(o?.avg_duration_ms_24h)} icon={<Timer className="size-4" />} loading={overview.isPending} hint="Successful attempts" />
        <StatCard label="Active workers" value={o?.workers.active ?? 0} icon={<Cpu className="size-4" />} tone={o && o.workers.active === 0 ? "danger" : "success"} loading={overview.isPending} hint={`${o?.workers.draining ?? 0} draining · ${o?.workers.offline ?? 0} offline (24h)`} />
        <StatCard label="Total tasks" value={compactNumber(o?.total_tasks)} icon={<Activity className="size-4" />} loading={overview.isPending} hint="Within the retention window" />
        <StatCard label="Queue wait p95 (1h)" value={duration(latency.data?.queue_wait.p95_ms)} icon={<Clock className="size-4" />} loading={latency.isPending} hint={<Link href="/analytics" className="underline-offset-2 hover:underline">Latency details</Link>} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartFrame
          className="xl:col-span-2"
          title="Throughput, last hour"
          subtitle="Tasks per minute"
          legend={THROUGHPUT_SERIES}
          refreshing={throughput.isFetching && !throughput.isPending}
          table={<SeriesTable data={series} series={THROUGHPUT_SERIES} formatTime={formatTime} />}
        >
          {throughput.isError ? (
            <ErrorState error={throughput.error} onRetry={() => void throughput.refetch()} />
          ) : (
            <TimeSeriesChart
              data={series}
              series={THROUGHPUT_SERIES}
              formatTime={formatTime}
              ariaLabel="Line chart of tasks submitted, succeeded and failed per minute over the last hour. Use the table view for exact values."
            />
          )}
        </ChartFrame>

        <ChartFrame
          title="Tasks by state"
          subtitle="All tasks in this project"
          refreshing={overview.isFetching && !overview.isPending}
          table={<BarTable data={stateBars} valueLabel="Tasks" />}
        >
          <BarList data={stateBars} emptyLabel="No tasks yet — submit one to get started." />
        </ChartFrame>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartFrame
          title="Queue depth"
          subtitle="QUEUED tasks per queue"
          table={<BarTable data={queueBars} valueLabel="Queued" />}
        >
          <BarList data={queueBars} emptyLabel="All queues are drained." />
        </ChartFrame>

        <GlassCard className="flex flex-col xl:col-span-1">
          <SectionTitle
            actions={<span className="text-xs text-muted-foreground">{realtime === "live" ? "Streaming" : "Waiting for stream…"}</span>}
          >
            Live activity
          </SectionTitle>
          <div className="max-h-[22rem] min-h-40 flex-1 overflow-y-auto px-3 pb-3" aria-live="polite">
            {activity.length === 0 ? (
              <EmptyState
                icon={<Activity className="size-5" />}
                title="Listening for events"
                description="Task, workflow and schedule changes in this project appear here as they happen."
              />
            ) : (
              activity.map((item) => <ActivityRow key={item.id} item={item} />)
            )}
          </div>
        </GlassCard>

        <GlassCard className="flex flex-col">
          <SectionTitle>System health</SectionTitle>
          <dl className="grid grid-cols-2 gap-4 px-5 pb-5 text-sm">
            {[
              ["API", !system.isError && !!system.data],
              ["PostgreSQL", system.data?.database],
              ["Redis", system.data?.redis],
              ["Live updates", realtime === "live"],
            ].map(([label, ok]) => (
              <div key={String(label)} className="glass-strong rounded-xl p-3">
                <dt className="text-xs text-muted-foreground">{String(label)}</dt>
                <dd className="mt-1 flex items-center gap-2 font-medium">
                  <span className={ok ? "size-2 rounded-full bg-success" : "size-2 rounded-full bg-destructive"} aria-hidden />
                  {ok ? "Healthy" : ok === undefined ? "Checking" : "Unavailable"}
                </dd>
              </div>
            ))}
            <div className="glass-strong col-span-2 rounded-xl p-3">
              <dt className="text-xs text-muted-foreground">Outbox backlog</dt>
              <dd className="mt-1 font-medium">
                {system.data?.outbox_backlog ?? "—"} events waiting to be dispatched
              </dd>
            </div>
            <div className="col-span-2 text-xs text-muted-foreground">
              Updated <RelativeTime value={o?.generated_at} />
            </div>
          </dl>
        </GlassCard>
      </div>
    </div>
  )
}
