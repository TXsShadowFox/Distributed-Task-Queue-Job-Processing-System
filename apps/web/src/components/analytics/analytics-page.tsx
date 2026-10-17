"use client"

import { AlertTriangle, Clock, Gauge, Hourglass, RotateCcw, Timer } from "lucide-react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { useMemo } from "react"

import {
  BarList,
  BarTable,
  ChartFrame,
  SeriesTable,
  TimeSeriesChart,
  timeFormatter,
  type SeriesSpec,
} from "@/components/charts/charts"
import { ErrorState, GlassCard, PageHeader, RelativeTime, StatCard } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useFailures, useLatency, useThroughput, useUtilization } from "@/lib/api/hooks"
import type { AnalyticsWindow } from "@/lib/api/types"
import { compactNumber, duration, percent } from "@/lib/format"
import { replaceQuery } from "@/lib/url-state"
import { cn } from "@/lib/utils"

const WINDOWS: { value: AnalyticsWindow; label: string; seconds: number }[] = [
  { value: "1h", label: "1h", seconds: 3_600 },
  { value: "6h", label: "6h", seconds: 21_600 },
  { value: "24h", label: "24h", seconds: 86_400 },
  { value: "7d", label: "7d", seconds: 604_800 },
  { value: "30d", label: "30d", seconds: 2_592_000 },
]

// Same identities and order as the overview, so a series keeps its color across pages.
const THROUGHPUT_SERIES: SeriesSpec[] = [
  { key: "succeeded", label: "Succeeded", slot: 1 },
  { key: "submitted", label: "Submitted", slot: 2 },
  { key: "failed", label: "Failed", slot: 3 },
]

const LATENCY_SERIES: SeriesSpec[] = [
  { key: "exec_p50_ms", label: "Execution p50", slot: 1 },
  { key: "exec_p95_ms", label: "Execution p95", slot: 2 },
  { key: "wait_p95_ms", label: "Queue wait p95", slot: 3 },
]

const formatMs = (v: number | null) => (v === null ? "—" : duration(v))

function WindowPicker({ value, onChange }: { value: AnalyticsWindow; onChange: (w: AnalyticsWindow) => void }) {
  return (
    <div className="glass flex rounded-xl p-1" role="radiogroup" aria-label="Time window">
      {WINDOWS.map((w) => (
        <button
          key={w.value}
          type="button"
          role="radio"
          aria-checked={value === w.value}
          onClick={() => onChange(w.value)}
          className={cn(
            "rounded-lg px-3 py-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground",
            value === w.value && "bg-primary/15 text-foreground shadow-sm",
          )}
        >
          {w.label}
        </button>
      ))}
    </div>
  )
}

function LatencyTable({ rows }: { rows: { task_type: string; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; avg_ms: number | null; count: number }[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Task type</TableHead>
          <TableHead className="text-right">p50</TableHead>
          <TableHead className="text-right">p95</TableHead>
          <TableHead className="text-right">p99</TableHead>
          <TableHead className="text-right">Avg</TableHead>
          <TableHead className="text-right">Runs</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.task_type}>
            <TableCell className="font-mono text-xs">{r.task_type}</TableCell>
            <TableCell className="text-right tabular-nums">{formatMs(r.p50_ms)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatMs(r.p95_ms)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatMs(r.p99_ms)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatMs(r.avg_ms)}</TableCell>
            <TableCell className="text-right tabular-nums">{r.count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function AnalyticsPage() {
  const params = useSearchParams()
  const pathname = usePathname()
  const requested = params.get("window") as AnalyticsWindow | null
  const range = WINDOWS.some((w) => w.value === requested) ? (requested as AnalyticsWindow) : "24h"
  const spanSeconds = WINDOWS.find((w) => w.value === range)?.seconds ?? 86_400
  const setWindow = (w: AnalyticsWindow) => replaceQuery(pathname, w === "24h" ? "" : `window=${w}`)

  const throughput = useThroughput(range)
  const latency = useLatency(range)
  const failures = useFailures(range)
  const utilization = useUtilization(range)

  const throughputRows = useMemo(() => (throughput.data?.series ?? []) as unknown as Record<string, number | string | null>[], [throughput.data])
  const latencyRows = useMemo(() => (latency.data?.series ?? []) as unknown as Record<string, number | string | null>[], [latency.data])
  const throughputTime = useMemo(() => timeFormatter(throughput.data?.bucket_seconds ?? 60, spanSeconds), [throughput.data?.bucket_seconds, spanSeconds])
  const latencyTime = useMemo(() => timeFormatter(latency.data?.bucket_seconds ?? 60, spanSeconds), [latency.data?.bucket_seconds, spanSeconds])

  const totals = useMemo(() => {
    const series = throughput.data?.series ?? []
    const sum = (key: "submitted" | "succeeded" | "failed" | "retried" | "cancelled") => series.reduce((a, b) => a + b[key], 0)
    return { submitted: sum("submitted"), succeeded: sum("succeeded"), failed: sum("failed"), retried: sum("retried") }
  }, [throughput.data])

  const finished = totals.succeeded + totals.failed
  const latencyBars = useMemo(
    () =>
      (latency.data?.by_task_type ?? [])
        .filter((t) => t.p95_ms !== null)
        .map((t) => ({ label: t.task_type, value: t.p95_ms ?? 0, hint: `p50 ${formatMs(t.p50_ms)} · ${t.count} runs` }))
        .sort((a, b) => b.value - a.value),
    [latency.data],
  )
  const failureBars = useMemo(
    () =>
      (failures.data?.by_task_type ?? [])
        .filter((t) => t.total > 0)
        .map((t) => ({ label: t.task_type, value: t.failure_rate, hint: `${t.failed} of ${t.total} tasks failed` }))
        .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label)),
    [failures.data],
  )
  const errorBars = useMemo(
    () =>
      (failures.data?.by_error_type ?? []).map((e) => ({
        label: e.error_type,
        value: e.count,
        hint: `attempts ending ${e.attempt_status.toLowerCase().replace("_", " ")}`,
      })),
    [failures.data],
  )
  const workerBars = useMemo(
    () =>
      (utilization.data?.workers ?? []).map((w) => ({
        label: w.name,
        value: w.utilization,
        hint: `${w.attempts} attempts · busy ${duration(w.busy_seconds * 1000)} over ${duration(w.online_seconds * 1000)} online × ${w.concurrency} slots`,
      })),
    [utilization.data],
  )

  const firstError = throughput.error ?? latency.error ?? failures.error ?? utilization.error
  const loadingTiles = throughput.isPending || latency.isPending

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description="Throughput, latency percentiles, failures and worker utilization, computed by PostgreSQL from task and attempt history."
        actions={<WindowPicker value={range} onChange={setWindow} />}
      />

      {firstError && (
        <ErrorState
          error={firstError}
          onRetry={() => {
            void throughput.refetch()
            void latency.refetch()
            void failures.refetch()
            void utilization.refetch()
          }}
        />
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Finished" value={compactNumber(finished)} icon={<Gauge className="size-4" />} loading={loadingTiles} hint={`${compactNumber(totals.submitted)} submitted`} />
        <StatCard
          label="Failure rate"
          value={finished ? percent(totals.failed / finished) : "—"}
          icon={<AlertTriangle className="size-4" />}
          tone={totals.failed ? "danger" : "default"}
          loading={loadingTiles}
          hint={`${compactNumber(totals.failed)} failed or dead-lettered`}
        />
        <StatCard label="Retries" value={compactNumber(totals.retried)} icon={<RotateCcw className="size-4" />} loading={loadingTiles} hint="Attempts after the first" />
        <StatCard
          label="Execution p95"
          value={formatMs(latency.data?.execution.p95_ms ?? null)}
          icon={<Timer className="size-4" />}
          loading={loadingTiles}
          hint={`p50 ${formatMs(latency.data?.execution.p50_ms ?? null)} · p99 ${formatMs(latency.data?.execution.p99_ms ?? null)}`}
        />
        <StatCard
          label="Queue wait p95"
          value={formatMs(latency.data?.queue_wait.p95_ms ?? null)}
          icon={<Hourglass className="size-4" />}
          loading={loadingTiles}
          hint={`p50 ${formatMs(latency.data?.queue_wait.p50_ms ?? null)}`}
        />
        <StatCard
          label="End-to-end p95"
          value={formatMs(latency.data?.end_to_end.p95_ms ?? null)}
          icon={<Clock className="size-4" />}
          loading={loadingTiles}
          hint="Submitted → finished"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame
          title="Throughput"
          subtitle={`Tasks per ${duration((throughput.data?.bucket_seconds ?? 60) * 1000)} bucket`}
          legend={THROUGHPUT_SERIES}
          refreshing={throughput.isFetching && !throughput.isPending}
          table={<SeriesTable data={throughputRows} series={THROUGHPUT_SERIES} formatTime={throughputTime} />}
        >
          <TimeSeriesChart
            data={throughputRows}
            series={THROUGHPUT_SERIES}
            formatTime={throughputTime}
            ariaLabel="Line chart of tasks submitted, succeeded and failed per time bucket. Use the table view for exact values."
          />
        </ChartFrame>
        <ChartFrame
          title="Latency"
          subtitle="Execution time of successful attempts and time spent queued, per bucket"
          legend={LATENCY_SERIES}
          refreshing={latency.isFetching && !latency.isPending}
          table={<SeriesTable data={latencyRows} series={LATENCY_SERIES} formatTime={latencyTime} formatValue={formatMs} />}
        >
          <TimeSeriesChart
            data={latencyRows}
            series={LATENCY_SERIES}
            formatTime={latencyTime}
            formatValue={formatMs}
            yAxisWidth={64}
            ariaLabel="Line chart of execution p50, execution p95 and queue-wait p95 per time bucket. Use the table view for exact values."
          />
        </ChartFrame>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartFrame
          title="Slowest task types"
          subtitle="p95 execution time"
          refreshing={latency.isFetching && !latency.isPending}
          table={<LatencyTable rows={latency.data?.by_task_type ?? []} />}
        >
          <BarList data={latencyBars} formatValue={(v) => duration(v)} emptyLabel="No successful attempts in this window." />
        </ChartFrame>
        <ChartFrame
          title="Failure rate by task type"
          subtitle="Share of finished tasks that failed"
          refreshing={failures.isFetching && !failures.isPending}
          table={<BarTable data={failureBars} valueLabel="Failure rate" formatValue={(v) => percent(v)} />}
        >
          <BarList data={failureBars} max={1} formatValue={(v) => percent(v)} emptyLabel="No finished tasks in this window." />
        </ChartFrame>
        <ChartFrame
          title="Errors by type"
          subtitle="Failed attempts, including ones that were retried"
          refreshing={failures.isFetching && !failures.isPending}
          table={<BarTable data={errorBars} valueLabel="Attempts" />}
        >
          <BarList data={errorBars} emptyLabel="No errors in this window." />
        </ChartFrame>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame
          title="Worker utilization"
          subtitle="Busy share of each worker's slots while it was online in this window"
          refreshing={utilization.isFetching && !utilization.isPending}
          table={<BarTable data={workerBars} valueLabel="Utilization" formatValue={(v) => percent(v, 2)} />}
        >
          <BarList data={workerBars} max={1} formatValue={(v) => percent(v, v < 0.01 ? 2 : 1)} emptyLabel="No worker was online in this window." />
        </ChartFrame>
        <GlassCard className="p-5">
          <h2 className="text-sm font-semibold tracking-tight">Recent failures</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">Latest tasks that ended failed or dead-lettered</p>
          {failures.data?.recent.length ? (
            <ul className="mt-2 divide-y divide-border">
              {failures.data.recent.map((f) => (
                <li key={f.task_id} className="flex items-start justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <Link href={`/tasks/${f.task_id}`} className="font-mono text-xs text-primary hover:underline">
                      {f.task_type}
                    </Link>
                    <p className="truncate text-sm" title={f.message}>
                      {f.error_type && <span className="font-mono text-xs text-muted-foreground">{f.error_type}: </span>}
                      {f.message || "No message"}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <StatusBadge status={f.state} />
                    <RelativeTime value={f.finished_at} className="text-xs text-muted-foreground" />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">No failures in this window.</p>
          )}
        </GlassCard>
      </div>
    </div>
  )
}
