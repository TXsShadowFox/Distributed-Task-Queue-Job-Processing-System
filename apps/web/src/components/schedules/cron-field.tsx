"use client"

import { AlertTriangle, CalendarClock, Loader2 } from "lucide-react"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useCronPreview } from "@/lib/api/hooks"
import { useDebounced } from "@/lib/use-debounced"
import { cn } from "@/lib/utils"

export const CRON_PRESETS = [
  { label: "Every 5 minutes", expression: "*/5 * * * *" },
  { label: "Hourly", expression: "0 * * * *" },
  { label: "Daily 09:00", expression: "0 9 * * *" },
  { label: "Weekdays 09:00", expression: "0 9 * * 1-5" },
  { label: "Mondays 08:00", expression: "0 8 * * 1" },
  { label: "1st of month", expression: "0 0 1 * *" },
]

/** Cron expression input with presets and a live, server-computed preview of the next fire times. */
export function CronField({
  expression,
  timezone,
  onChange,
  disabled,
}: {
  expression: string
  timezone: string
  onChange: (expression: string) => void
  disabled?: boolean
}) {
  const debouncedExpression = useDebounced(expression.trim(), 300)
  const debouncedZone = useDebounced(timezone, 300)
  const preview = useCronPreview(debouncedExpression, debouncedZone)
  const settling = debouncedExpression !== expression.trim() || debouncedZone !== timezone
  const invalid = preview.error instanceof ApiError && preview.error.status === 422

  return (
    <div className="space-y-2">
      <Label htmlFor="cron-expression">Cron expression</Label>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Presets">
        {CRON_PRESETS.map((p) => (
          <button
            key={p.expression}
            type="button"
            disabled={disabled}
            onClick={() => onChange(p.expression)}
            aria-pressed={expression.trim() === p.expression}
            className={cn(
              "rounded-full border border-border px-2.5 py-0.5 text-xs transition-colors hover:bg-muted disabled:opacity-50",
              expression.trim() === p.expression && "border-primary/50 bg-primary/10 text-primary",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>
      <Input
        id="cron-expression"
        value={expression}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder="minute hour day month weekday — e.g. 30 9 * * 1-5"
        spellCheck={false}
        autoComplete="off"
        aria-invalid={invalid}
        aria-describedby="cron-help"
        className="font-mono"
      />
      <div id="cron-help" className="min-h-5 text-xs" aria-live="polite">
        {!expression.trim() ? (
          <span className="text-muted-foreground">Five fields: minute, hour, day of month, month, day of week. @daily and @hourly work too.</span>
        ) : settling || preview.isFetching ? (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Loader2 className="size-3 animate-spin" aria-hidden /> Checking…
          </span>
        ) : preview.isError ? (
          <span className="text-destructive">{invalid ? errorMessage(preview.error) : `Could not preview: ${errorMessage(preview.error)}`}</span>
        ) : preview.data ? (
          <span className="font-medium">{preview.data.description}</span>
        ) : null}
      </div>

      {preview.data && !preview.isError && !settling && (
        <div className="rounded-lg border border-border bg-muted/30 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium">
            <CalendarClock className="size-3.5" aria-hidden /> Next runs ({preview.data.timezone})
          </p>
          <ol className="space-y-1 text-xs tabular-nums">
            {preview.data.next_runs.map((run) => (
              <li key={run.utc} className="flex flex-wrap justify-between gap-x-3">
                <span>{formatLocalFireTime(run.local)}</span>
                <span className="text-muted-foreground">{formatUtc(run.utc)}</span>
              </li>
            ))}
          </ol>
          {preview.data.dst_note && (
            <p className="mt-2 flex gap-1.5 text-xs text-warning">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {preview.data.dst_note}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** "2026-10-12 02:30 EDT" → "Mon, 12 Oct 2026, 02:30 EDT". The server already rendered it in the schedule's zone. */
export function formatLocalFireTime(text: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})\s*(.*)$/.exec(text)
  if (!match) return text
  const [, y, mo, d, h, mi, zone] = match
  const day = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d))).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  })
  return `${day}, ${h}:${mi}${zone ? ` ${zone}` : ""}`
}

function formatUtc(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`
}
