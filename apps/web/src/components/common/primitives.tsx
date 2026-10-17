"use client"

import { AlertTriangle, Check, Copy, Inbox, RefreshCw } from "lucide-react"
import { useState, useSyncExternalStore, type ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { errorMessage } from "@/lib/api/client"
import { dateTime, relativeTime } from "@/lib/format"
import { cn } from "@/lib/utils"

export function GlassCard({ className, children, ...props }: React.ComponentProps<"section">) {
  return (
    <section className={cn("glass rounded-2xl", className)} {...props}>
      {children}
    </section>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 px-5 pt-4 pb-2">
      <h2 className="text-sm font-semibold tracking-tight">{children}</h2>
      {actions}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-12 text-center", className)}>
      <div className="mb-1 flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        {icon ?? <Inbox className="size-5" aria-hidden />}
      </div>
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-2 px-6 py-10 text-center", className)}>
      <div className="flex size-11 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <p className="font-medium">Couldn&apos;t load this data</p>
      <p className="max-w-md text-sm text-muted-foreground">{errorMessage(error)}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
          <RefreshCw /> Try again
        </Button>
      )}
    </div>
  )
}

export function RowsSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2 p-4", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full rounded-lg" />
      ))}
    </div>
  )
}

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "default",
  loading,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  tone?: "default" | "success" | "danger" | "warning" | "info"
  loading?: boolean
}) {
  const toneClass = {
    default: "text-primary bg-primary/10",
    success: "text-success bg-success/10",
    danger: "text-destructive bg-destructive/10",
    warning: "text-warning bg-warning/10",
    info: "text-info bg-info/10",
  }[tone]
  return (
    <GlassCard className="p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
        {icon && <span className={cn("flex size-8 items-center justify-center rounded-lg", toneClass)}>{icon}</span>}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-24" />
      ) : (
        <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      )}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </GlassCard>
  )
}

export function CopyButton({ value, label = "Copy", className }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className={className}
          aria-label={label}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value)
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            } catch {
              toast.error("Clipboard unavailable — copy manually")
            }
          }}
        >
          {copied ? <Check className="text-success" /> : <Copy />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? "Copied" : label}</TooltipContent>
    </Tooltip>
  )
}

export function MonoId({ value, full = false, className }: { value: string; full?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 font-mono text-xs", className)}>
      <span title={value}>{full ? value : `${value.slice(0, 8)}…${value.slice(-4)}`}</span>
      <CopyButton value={value} label="Copy ID" />
    </span>
  )
}

// One shared clock for every relative timestamp: ticks every 15 s while anything is subscribed. The server snapshot is
// null, so relative times render only in the browser (they depend on the viewer's clock) and hydration never mismatches.
const clockListeners = new Set<() => void>()
let clockNow = 0
let clockTimer: ReturnType<typeof setInterval> | undefined

function subscribeClock(listener: () => void) {
  clockListeners.add(listener)
  if (!clockTimer) {
    clockNow = Date.now()
    queueMicrotask(() => clockListeners.forEach((fn) => fn()))
    clockTimer = setInterval(() => {
      clockNow = Date.now()
      clockListeners.forEach((fn) => fn())
    }, 15_000)
  }
  return () => {
    clockListeners.delete(listener)
    if (!clockListeners.size && clockTimer) {
      clearInterval(clockTimer)
      clockTimer = undefined
    }
  }
}

export function useNow(): number | null {
  return useSyncExternalStore(subscribeClock, () => clockNow || null, () => null)
}

export function RelativeTime({ value, className }: { value: string | null | undefined; className?: string }) {
  const now = useNow()
  if (!value) return <span className={className}>—</span>
  return (
    <time dateTime={value} title={dateTime(value)} className={cn("whitespace-nowrap", className)}>
      {now === null ? " " : relativeTime(value, now)}
    </time>
  )
}

export function JsonView({ value, className, maxHeight = "24rem" }: { value: unknown; className?: string; maxHeight?: string }) {
  const text = value === undefined ? "—" : JSON.stringify(value, null, 2)
  return (
    <div className={cn("group relative", className)}>
      <pre
        className="glass-strong overflow-auto rounded-xl p-4 font-mono text-xs leading-relaxed"
        style={{ maxHeight }}
        tabIndex={0}
      >
        {text}
      </pre>
      <div className="absolute top-2 right-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <CopyButton value={text} label="Copy JSON" />
      </div>
    </div>
  )
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm">{children}</dd>
    </div>
  )
}
