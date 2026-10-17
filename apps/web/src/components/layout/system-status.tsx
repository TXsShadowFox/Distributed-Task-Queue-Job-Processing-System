"use client"

import { Activity } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useSystemStatus } from "@/lib/api/hooks"
import { useRealtime } from "@/lib/realtime"
import { cn } from "@/lib/utils"

function Row({ label, ok, detail }: { label: string; ok: boolean | null; detail?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-2">
        {detail && <span className="text-xs text-muted-foreground">{detail}</span>}
        <span
          className={cn("size-2 rounded-full", ok === null ? "bg-muted-foreground" : ok ? "bg-success" : "bg-destructive")}
          aria-hidden
        />
        <span className="sr-only">{ok ? "healthy" : "unhealthy"}</span>
      </span>
    </div>
  )
}

export function SystemStatusPill() {
  const { data, isError } = useSystemStatus()
  const realtime = useRealtime((s) => s.status)
  const state = isError ? "outage" : (data?.status ?? "unknown")
  const label = { operational: "Operational", degraded: "Degraded", outage: "Outage", unknown: "Checking…" }[state]
  const color = {
    operational: "bg-success",
    degraded: "bg-warning",
    outage: "bg-destructive",
    unknown: "bg-muted-foreground",
  }[state]
  const active = data?.workers?.ACTIVE ?? 0

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" className="h-9 gap-2 px-2 sm:px-3" aria-label={`System status: ${label}`}>
          <span className="relative flex size-2">
            {state === "operational" && <span className={cn("absolute size-full animate-ping rounded-full opacity-40", color)} />}
            <span className={cn("relative size-2 rounded-full", color)} />
          </span>
          <span className="hidden text-sm md:inline">{label}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <div className="flex items-center gap-2 font-medium">
          <Activity className="size-4 text-primary" /> System status
        </div>
        <div className="mt-2 divide-y divide-border">
          <Row label="API" ok={!isError && !!data} />
          <Row label="PostgreSQL" ok={data ? data.database : null} />
          <Row label="Redis" ok={data ? data.redis : null} />
          <Row label="Live updates" ok={realtime === "live"} detail={realtime} />
          <Row label="Workers" ok={data ? active > 0 : null} detail={data ? `${active} active` : undefined} />
          <Row
            label="Outbox backlog"
            ok={data ? (data.outbox_backlog ?? 0) < 1000 : null}
            detail={data?.outbox_backlog !== null && data?.outbox_backlog !== undefined ? String(data.outbox_backlog) : undefined}
          />
        </div>
        {state === "degraded" && active === 0 && (
          <p className="mt-3 text-xs text-muted-foreground">No active workers: queued tasks will wait until one starts.</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
