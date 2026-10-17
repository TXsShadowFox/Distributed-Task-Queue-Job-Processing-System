"use client"

/**
 * Chart primitives following the dashboard's data-viz rules:
 * - series colors come from validated tokens (--series-1..3), identity by fixed order, never by rank;
 * - 2px lines, hairline solid grid, one y-axis, compact ticks; bars <= 24px with 4px rounded data-ends;
 * - a legend for 2+ series, a crosshair tooltip where values lead, and a table view for every chart;
 * - text always uses text tokens, never the series color.
 */
import { BarChart3, Table2 } from "lucide-react"
import { useId, useState, type ReactNode } from "react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { GlassCard } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip as UiTooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { compactNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

export interface SeriesSpec {
  key: string
  label: string
  /** 1-3: index into the validated categorical palette. */
  slot: 1 | 2 | 3
}

const SLOT_VAR = { 1: "var(--series-1)", 2: "var(--series-2)", 3: "var(--series-3)" } as const

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

// ------------------------------------------------------------------------------------------------ frame
export function ChartFrame({
  title,
  subtitle,
  legend,
  table,
  children,
  refreshing,
  className,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  legend?: SeriesSpec[]
  table: ReactNode
  children: ReactNode
  refreshing?: boolean
  className?: string
  actions?: ReactNode
}) {
  const [view, setView] = useState<"chart" | "table">("chart")
  const headingId = useId()
  return (
    <GlassCard className={cn("flex flex-col p-5", className)} aria-labelledby={headingId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id={headingId} className="text-sm font-semibold tracking-tight">
            {title}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-1">
          {actions}
          <div className="flex rounded-lg border border-border p-0.5" role="group" aria-label="Chart or table view">
            <UiTooltip>
              <TooltipTrigger asChild>
                <Button
                  variant={view === "chart" ? "secondary" : "ghost"}
                  size="icon-xs"
                  aria-pressed={view === "chart"}
                  aria-label="Chart view"
                  onClick={() => setView("chart")}
                >
                  <BarChart3 />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Chart</TooltipContent>
            </UiTooltip>
            <UiTooltip>
              <TooltipTrigger asChild>
                <Button
                  variant={view === "table" ? "secondary" : "ghost"}
                  size="icon-xs"
                  aria-pressed={view === "table"}
                  aria-label="Table view"
                  onClick={() => setView("table")}
                >
                  <Table2 />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Table</TooltipContent>
            </UiTooltip>
          </div>
        </div>
      </div>
      {legend && legend.length > 1 && view === "chart" && (
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
          {legend.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5">
              <span className="h-0.5 w-4 rounded-full" style={{ background: SLOT_VAR[s.slot] }} aria-hidden />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {/* Refetch keeps the frame: the previous render stays visible at reduced opacity. */}
      <div className={cn("mt-3 flex-1 transition-opacity", refreshing && "opacity-60")}>
        {view === "chart" ? children : <div className="max-h-80 overflow-auto">{table}</div>}
      </div>
    </GlassCard>
  )
}

// ------------------------------------------------------------------------------------------------ time series
type Row = Record<string, number | string | null>

interface TimeTooltipProps {
  active?: boolean
  payload?: ReadonlyArray<{ payload?: unknown }>
  label?: unknown
  series: SeriesSpec[]
  formatValue: (v: number | null) => string
  formatTime: (ts: string) => string
}

function TimeTooltip({ active, payload, label, series, formatValue, formatTime }: TimeTooltipProps) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload as Row
  return (
    <div className="glass-strong rounded-lg px-3 py-2 text-xs shadow-lg">
      <p className="mb-1.5 text-muted-foreground">{formatTime(String(label))}</p>
      <ul className="space-y-1">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <span className="h-0.5 w-3 rounded-full" style={{ background: SLOT_VAR[s.slot] }} aria-hidden />
            <span className="font-semibold tabular-nums">{formatValue(row[s.key] as number | null)}</span>
            <span className="text-muted-foreground">{s.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function TimeSeriesChart({
  data,
  series,
  formatValue = (v) => (v === null ? "—" : compactNumber(v)),
  formatTime,
  height = 240,
  ariaLabel,
  yAxisWidth = 56,
}: {
  data: Row[]
  series: SeriesSpec[]
  formatValue?: (v: number | null) => string
  formatTime: (ts: string) => string
  height?: number
  ariaLabel: string
  /** Room for y tick labels; widen it for long labels such as durations. */
  yAxisWidth?: number
}) {
  const animate = !prefersReducedMotion()
  return (
    // The container includes the x-axis band, so tick labels never cause a nested scroll.
    <div style={{ height }} role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: yAxisWidth > 56 ? 0 : -12 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeWidth={1} />
          <XAxis
            dataKey="ts"
            tickFormatter={formatTime}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            axisLine={{ stroke: "var(--chart-axis)" }}
            tickLine={false}
            minTickGap={28}
          />
          <YAxis
            tickFormatter={(v: number) => formatValue(v)}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={yAxisWidth}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)", strokeWidth: 1 }}
            content={(props) => (
              <TimeTooltip
                active={props.active}
                payload={props.payload}
                label={props.label}
                series={series}
                formatValue={formatValue}
                formatTime={formatTime}
              />
            )}
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={SLOT_VAR[s.slot]}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              // Empty buckets stay gaps (a line across them would invent values); a point with no neighbours
              // gets a marker so it does not vanish.
              dot={(props: { cx?: number; cy?: number; index?: number }) => {
                const i = props.index ?? 0
                const isolated =
                  data[i]?.[s.key] != null && data[i - 1]?.[s.key] == null && data[i + 1]?.[s.key] == null
                return isolated && props.cx != null && props.cy != null ? (
                  <circle key={`${s.key}-${i}`} cx={props.cx} cy={props.cy} r={4} fill={SLOT_VAR[s.slot]} stroke="var(--background)" strokeWidth={2} />
                ) : (
                  <g key={`${s.key}-${i}`} />
                )
              }}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--background)" }}
              connectNulls={false}
              isAnimationActive={animate}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function SeriesTable({
  data,
  series,
  formatTime,
  formatValue = (v) => (v === null ? "—" : String(v)),
}: {
  data: Row[]
  series: SeriesSpec[]
  formatTime: (ts: string) => string
  formatValue?: (v: number | null) => string
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          {series.map((s) => (
            <TableHead key={s.key} className="text-right">
              {s.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {[...data].reverse().map((row) => (
          <TableRow key={String(row.ts)}>
            <TableCell className="text-muted-foreground">{formatTime(String(row.ts))}</TableCell>
            {series.map((s) => (
              <TableCell key={s.key} className="text-right tabular-nums">
                {formatValue(row[s.key] as number | null)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ------------------------------------------------------------------------------------------------ horizontal bars
export interface BarDatum {
  label: string
  value: number
  hint?: string
  href?: string
}

/** One series -> one color (slot 1) for every bar. Values sit at the bar tip; the row is the hover/focus target. */
export function BarList({
  data,
  max,
  formatValue = (v) => compactNumber(v),
  emptyLabel = "No data yet",
}: {
  data: BarDatum[]
  max?: number
  formatValue?: (v: number) => string
  emptyLabel?: string
}) {
  if (!data.length) return <p className="py-8 text-center text-sm text-muted-foreground">{emptyLabel}</p>
  const top = max ?? Math.max(...data.map((d) => d.value), 1)
  return (
    <ul className="space-y-2.5">
      {data.map((d) => {
        const pct = Math.max(0, Math.min(100, (d.value / top) * 100))
        return (
          <li key={d.label}>
            <UiTooltip>
              <TooltipTrigger asChild>
                <div
                  tabIndex={0}
                  className="group grid grid-cols-[minmax(0,9rem)_1fr] items-center gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`${d.label}: ${formatValue(d.value)}${d.hint ? `, ${d.hint}` : ""}`}
                >
                  <span className="truncate text-xs text-muted-foreground">{d.label}</span>
                  <div className="flex min-w-0 items-center gap-2">
                    <div className="h-3.5 flex-1">
                      <div
                        className="h-full rounded-r-[4px] transition-[width,filter] duration-300 group-hover:brightness-110"
                        style={{ width: `${pct}%`, minWidth: d.value > 0 ? 3 : 0, background: "var(--series-1)" }}
                      />
                    </div>
                    <span className="w-14 shrink-0 text-right text-xs font-medium tabular-nums">{formatValue(d.value)}</span>
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent side="top">
                <span className="font-semibold">{formatValue(d.value)}</span> <span className="opacity-80">{d.label}</span>
                {d.hint && <span className="block opacity-80">{d.hint}</span>}
              </TooltipContent>
            </UiTooltip>
          </li>
        )
      })}
    </ul>
  )
}

export function BarTable({ data, formatValue = (v) => String(v), valueLabel = "Value" }: {
  data: BarDatum[]
  formatValue?: (v: number) => string
  valueLabel?: string
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead className="text-right">{valueLabel}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((d) => (
          <TableRow key={d.label}>
            <TableCell>{d.label}</TableCell>
            <TableCell className="text-right tabular-nums">{formatValue(d.value)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Axis/table labels for bucket timestamps; includes the date for coarse buckets or windows spanning days. */
export function timeFormatter(bucketSeconds: number, spanSeconds = 0): (ts: string) => string {
  const withDate = bucketSeconds >= 3600 || spanSeconds > 12 * 3600
  const fmt = new Intl.DateTimeFormat(undefined, withDate
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" })
  return (ts: string) => {
    const d = new Date(ts)
    return Number.isNaN(d.getTime()) ? ts : fmt.format(d)
  }
}
