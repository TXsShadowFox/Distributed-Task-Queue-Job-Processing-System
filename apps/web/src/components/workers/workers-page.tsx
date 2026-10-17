"use client"

import { Cpu, Gauge, HardDrive, Pause, Play, Server, ServerOff, ShieldAlert, Timer } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import {
  EmptyState,
  ErrorState,
  GlassCard,
  KeyValue,
  PageHeader,
  RelativeTime,
  StatCard,
  useNow,
} from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { errorMessage } from "@/lib/api/client"
import { useWorkerDetail, useWorkerDrain, useWorkers } from "@/lib/api/hooks"
import type { Worker } from "@/lib/api/types"
import { bytes, dateTime, duration } from "@/lib/format"
import { useCan } from "@/lib/session"
import { cn } from "@/lib/utils"

/** One square per execution slot, filled while busy. The count is also given as text. */
function SlotMeter({ busy, total }: { busy: number; total: number }) {
  const shown = Math.min(total, 32)
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-0.5" aria-hidden>
        {Array.from({ length: shown }, (_, i) => (
          <span key={i} className={cn("size-2.5 rounded-[3px]", i < busy ? "bg-primary" : "bg-muted ring-1 ring-border ring-inset")} />
        ))}
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">
        {busy} of {total} slots busy
      </p>
    </div>
  )
}

function uptime(startedAt: string, now: number | null): string {
  return now === null ? "—" : duration(Math.max(0, now - Date.parse(startedAt)))
}

function DrainButton({ worker, size = "sm" }: { worker: Worker; size?: "sm" | "default" }) {
  const drain = useWorkerDrain()
  if (worker.status === "DRAINING") {
    return (
      <Button
        variant="outline"
        size={size}
        disabled={drain.isPending}
        onClick={() =>
          drain.mutate(
            { workerId: worker.id, drain: false },
            {
              onSuccess: () => toast.success(`${worker.name} is taking new tasks again`),
              onError: (e) => toast.error(errorMessage(e)),
            },
          )
        }
      >
        <Play /> Resume
      </Button>
    )
  }
  if (worker.status !== "ACTIVE") return null
  return (
    <Confirm
      trigger={
        <Button variant="outline" size={size} disabled={drain.isPending}>
          <Pause /> Drain
        </Button>
      }
      title={`Drain ${worker.name}?`}
      description="It stops taking new tasks, finishes the ones it is running and hands queued messages back to the stream for other workers. Resume it at any time."
      confirmLabel="Drain worker"
      onConfirm={() =>
        drain.mutate(
          { workerId: worker.id, drain: true },
          {
            onSuccess: () => toast.success(`Draining ${worker.name}`),
            onError: (e) => toast.error(errorMessage(e)),
          },
        )
      }
    />
  )
}

function WorkerCard({ worker, onOpen }: { worker: Worker; onOpen: () => void }) {
  const can = useCan()
  const now = useNow()
  const live = worker.status === "ACTIVE" || worker.status === "DRAINING"
  return (
    <GlassCard className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button type="button" onClick={onOpen} className="truncate text-left font-medium hover:underline">
            {worker.name}
          </button>
          <p className="truncate text-xs text-muted-foreground">
            {worker.hostname} · pid {worker.pid} · v{worker.version}
          </p>
        </div>
        <StatusBadge status={worker.status} />
      </div>

      {live && <SlotMeter busy={worker.active_tasks} total={worker.concurrency} />}

      <dl className="grid grid-cols-3 gap-3 text-sm">
        <div>
          <dt className="flex items-center gap-1 text-xs text-muted-foreground">
            <Cpu className="size-3" aria-hidden /> CPU
          </dt>
          <dd className="tabular-nums">{worker.cpu_percent == null ? "—" : `${worker.cpu_percent.toFixed(1)}%`}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1 text-xs text-muted-foreground">
            <HardDrive className="size-3" aria-hidden /> Memory
          </dt>
          <dd className="tabular-nums">{bytes(worker.memory_rss_bytes)}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1 text-xs text-muted-foreground">
            <Timer className="size-3" aria-hidden /> Uptime
          </dt>
          <dd className="tabular-nums">{live ? uptime(worker.started_at, now) : "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Succeeded</dt>
          <dd className="tabular-nums">{worker.tasks_succeeded.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Failed</dt>
          <dd className="tabular-nums">{worker.tasks_failed.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Heartbeat</dt>
          <dd>
            <RelativeTime value={worker.last_heartbeat_at} />
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap gap-1">
        {worker.queues.map((q) => (
          <span key={q} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            {q}
          </span>
        ))}
      </div>

      <div className="mt-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onOpen}>
          Details
        </Button>
        {can.platformAdmin && <DrainButton worker={worker} />}
      </div>
    </GlassCard>
  )
}

function WorkerSheet({ workerId, onClose }: { workerId: string | null; onClose: () => void }) {
  const { data: worker, isPending, isError, error, refetch } = useWorkerDetail(workerId)
  const can = useCan()
  return (
    <Sheet open={!!workerId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{worker?.name ?? "Worker"}</SheetTitle>
          <SheetDescription>
            {worker ? `${worker.hostname} · pid ${worker.pid} · version ${worker.version}` : "Loading…"}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-6 px-4 pb-6">
          {isError ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <StatusBadge status={worker.status} />
                {can.platformAdmin && <DrainButton worker={worker} size="default" />}
              </div>
              <dl className="grid grid-cols-2 gap-4">
                <KeyValue label="Started">{dateTime(worker.started_at)}</KeyValue>
                <KeyValue label="Last heartbeat">
                  <RelativeTime value={worker.last_heartbeat_at} />
                </KeyValue>
                <KeyValue label="Concurrency">{worker.concurrency} slots</KeyValue>
                <KeyValue label="Running now">{worker.active_tasks}</KeyValue>
                {worker.drain_requested_at && <KeyValue label="Drain requested">{dateTime(worker.drain_requested_at)}</KeyValue>}
                {worker.stopped_at && <KeyValue label="Stopped">{dateTime(worker.stopped_at)}</KeyValue>}
              </dl>
              <section className="space-y-2">
                <h3 className="text-sm font-medium">Consumes queues</h3>
                <div className="flex flex-wrap gap-1">
                  {worker.queues.map((q) => (
                    <span key={q} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs">
                      {q}
                    </span>
                  ))}
                </div>
              </section>
              <section className="space-y-2">
                <h3 className="text-sm font-medium">Registered task types · {worker.task_types.length}</h3>
                <ul className="grid grid-cols-2 gap-1 font-mono text-xs text-muted-foreground">
                  {worker.task_types.map((t) => (
                    <li key={t} className="truncate">
                      {t}
                    </li>
                  ))}
                </ul>
              </section>
              <section className="space-y-2">
                <h3 className="text-sm font-medium">Running tasks from this project</h3>
                {worker.project_tasks?.length ? (
                  <ul className="space-y-1.5">
                    {worker.project_tasks.map((t) => (
                      <li key={t.id} className="flex items-center justify-between gap-3 text-sm">
                        <Link href={`/tasks/${t.id}`} className="truncate font-mono text-xs text-primary hover:underline">
                          {t.task_type} · {t.id.slice(0, 8)}…
                        </Link>
                        <StatusBadge status={t.state} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">None right now.</p>
                )}
              </section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export function WorkersPage() {
  const { data, isPending, isError, error, refetch } = useWorkers()
  const can = useCan()
  const [openId, setOpenId] = useState<string | null>(null)
  const [showStopped, setShowStopped] = useState(false)

  const workers = data ?? []
  const live = workers.filter((w) => w.status === "ACTIVE" || w.status === "DRAINING")
  const capacity = workers.filter((w) => w.status === "ACTIVE").reduce((sum, w) => sum + w.concurrency, 0)
  const busy = live.reduce((sum, w) => sum + w.active_tasks, 0)
  const stopped = workers.filter((w) => w.status === "STOPPED").length
  const shown = showStopped ? workers : workers.filter((w) => w.status !== "STOPPED")

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workers"
        description="Processes consuming the Redis streams. Each heartbeat renews the leases of the tasks a worker is running; if a worker dies, its leases expire and those tasks are retried elsewhere."
        actions={
          stopped > 0 && (
            <Label className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
              <Switch checked={showStopped} onCheckedChange={setShowStopped} /> Show stopped ({stopped})
            </Label>
          )
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Active" value={workers.filter((w) => w.status === "ACTIVE").length} loading={isPending} icon={<Server className="size-4" />} />
        <StatCard label="Draining" value={workers.filter((w) => w.status === "DRAINING").length} loading={isPending} icon={<Pause className="size-4" />} />
        <StatCard
          label="Offline"
          value={workers.filter((w) => w.status === "OFFLINE").length}
          loading={isPending}
          icon={<ServerOff className="size-4" />}
          tone={workers.some((w) => w.status === "OFFLINE") ? "warning" : "default"}
        />
        <StatCard label="Busy slots" value={`${busy} / ${capacity}`} hint="across active workers" loading={isPending} icon={<Gauge className="size-4" />} />
      </div>

      {!can.platformAdmin && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <ShieldAlert className="size-3.5" aria-hidden /> Workers are shared by every project, so only platform administrators can drain or resume them.
        </p>
      )}

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-72 rounded-2xl" />
          ))}
        </div>
      ) : !shown.length ? (
        <GlassCard>
          <EmptyState
            icon={<Server className="size-5" />}
            title="No workers are running"
            description="Start one with `python -m flowforge_worker` (or `make worker`). Tasks wait in their queues until a worker picks them up."
          />
        </GlassCard>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((w) => (
            <WorkerCard key={w.id} worker={w} onOpen={() => setOpenId(w.id)} />
          ))}
        </div>
      )}

      <WorkerSheet workerId={openId} onClose={() => setOpenId(null)} />
    </div>
  )
}
