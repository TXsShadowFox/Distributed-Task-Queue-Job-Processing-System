import { cn } from "@/lib/utils"

const STYLES: Record<string, { label: string; className: string; dot: string; pulse?: boolean }> = {
  CREATED: { label: "Created", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  SCHEDULED: { label: "Scheduled", className: "bg-amber-500/12 text-amber-700 dark:text-amber-300 ring-amber-500/30", dot: "bg-amber-400" },
  QUEUED: { label: "Queued", className: "bg-sky-500/12 text-sky-700 dark:text-sky-300 ring-sky-500/30", dot: "bg-sky-400" },
  RUNNING: { label: "Running", className: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300 ring-indigo-500/35", dot: "bg-indigo-400", pulse: true },
  RETRY_SCHEDULED: { label: "Retrying", className: "bg-orange-500/12 text-orange-700 dark:text-orange-300 ring-orange-500/30", dot: "bg-orange-400" },
  SUCCEEDED: { label: "Succeeded", className: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30", dot: "bg-emerald-400" },
  FAILED: { label: "Failed", className: "bg-rose-500/12 text-rose-700 dark:text-rose-300 ring-rose-500/30", dot: "bg-rose-400" },
  CANCEL_REQUESTED: { label: "Cancelling", className: "bg-orange-500/12 text-orange-700 dark:text-orange-300 ring-orange-500/30", dot: "bg-orange-400", pulse: true },
  CANCELLED: { label: "Cancelled", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  DEAD_LETTERED: { label: "Dead-lettered", className: "bg-rose-600/15 text-rose-700 dark:text-rose-300 ring-rose-600/35", dot: "bg-rose-500" },
  // workflow runs / nodes
  PENDING: { label: "Pending", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  SKIPPED: { label: "Skipped", className: "bg-slate-500/10 text-slate-500 dark:text-slate-400 ring-slate-500/20", dot: "bg-slate-500" },
  // workers & schedules
  ACTIVE: { label: "Active", className: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30", dot: "bg-emerald-400", pulse: true },
  DRAINING: { label: "Draining", className: "bg-amber-500/12 text-amber-700 dark:text-amber-300 ring-amber-500/30", dot: "bg-amber-400" },
  STOPPED: { label: "Stopped", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  OFFLINE: { label: "Offline", className: "bg-rose-500/12 text-rose-700 dark:text-rose-300 ring-rose-500/30", dot: "bg-rose-400" },
  PAUSED: { label: "Paused", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  // API keys
  EXPIRING: { label: "Expiring", className: "bg-amber-500/12 text-amber-700 dark:text-amber-300 ring-amber-500/30", dot: "bg-amber-400" },
  EXPIRED: { label: "Expired", className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25", dot: "bg-slate-400" },
  REVOKED: { label: "Revoked", className: "bg-rose-500/12 text-rose-700 dark:text-rose-300 ring-rose-500/30", dot: "bg-rose-400" },
}

/** `animate={false}` drops the pulse, for states that are not live activity (an "active" API key, say). */
export function StatusBadge({ status, className, animate = true }: { status: string; className?: string; animate?: boolean }) {
  const style = STYLES[status] ?? {
    label: status,
    className: "bg-slate-500/15 text-slate-600 dark:text-slate-300 ring-slate-500/25",
    dot: "bg-slate-400",
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
        style.className,
        className,
      )}
      data-status={status}
    >
      <span className="relative flex size-1.5" aria-hidden>
        {style.pulse && animate && <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", style.dot)} />}
        <span className={cn("relative inline-flex size-1.5 rounded-full", style.dot)} />
      </span>
      {style.label}
    </span>
  )
}
