"use client"

import { Archive, GitFork, MoreHorizontal, Pencil, Play, Plus, Workflow as WorkflowIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import {
  EmptyState,
  ErrorState,
  GlassCard,
  PageHeader,
  RelativeTime,
  RowsSkeleton,
  SectionTitle,
} from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { RunWorkflowDialog } from "@/components/workflows/run-workflow-dialog"
import { errorMessage } from "@/lib/api/client"
import { useArchiveWorkflow, useWorkflowRuns, useWorkflows } from "@/lib/api/hooks"
import type { RunSummary, Workflow } from "@/lib/api/types"
import { duration } from "@/lib/format"
import { useCan } from "@/lib/session"

const RUN_STATES = ["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
const ANY = "__any__"

/** "4 of 5 nodes done" plus a thin bar; failures are spelled out, never shown by color alone. */
export function NodeProgress({ counts }: { counts: Record<string, number> | undefined }) {
  const total = Object.values(counts ?? {}).reduce((a, b) => a + b, 0)
  if (!total) return <span className="text-muted-foreground">—</span>
  const done = (counts?.SUCCEEDED ?? 0) + (counts?.SKIPPED ?? 0)
  const failed = counts?.FAILED ?? 0
  return (
    <div className="min-w-28 space-y-1">
      <div className="flex justify-between gap-2 text-xs tabular-nums">
        <span>
          {done} of {total} done
        </span>
        {failed > 0 && <span className="text-destructive">{failed} failed</span>}
      </div>
      <div className="flex h-1 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className="bg-success" style={{ width: `${((counts?.SUCCEEDED ?? 0) / total) * 100}%` }} />
        <div className="bg-destructive" style={{ width: `${(failed / total) * 100}%` }} />
      </div>
    </div>
  )
}

function runDuration(run: RunSummary): string {
  if (!run.started_at) return "—"
  const end = run.finished_at ? Date.parse(run.finished_at) : null
  return end ? duration(end - Date.parse(run.started_at)) : "running"
}

function WorkflowCard({ workflow, onRun }: { workflow: Workflow; onRun: (wf: Workflow) => void }) {
  const can = useCan()
  const archive = useArchiveWorkflow()
  const router = useRouter()
  const archived = !!workflow.archived_at
  return (
    <GlassCard className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <WorkflowIcon className="size-4.5" aria-hidden />
          </span>
          <div className="min-w-0">
            <Link href={`/workflows/${workflow.id}`} className="font-medium hover:underline">
              {workflow.name}
            </Link>
            <p className="line-clamp-2 text-sm text-muted-foreground">{workflow.description || "No description"}</p>
          </div>
        </div>
        <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
          v{workflow.latest_version}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Nodes</dt>
          <dd className="tabular-nums">{workflow.node_count ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Updated</dt>
          <dd>
            <RelativeTime value={workflow.updated_at} />
          </dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-muted-foreground">Last run</dt>
          <dd className="mt-1">
            {workflow.last_run ? (
              <Link href={`/workflows/runs/${workflow.last_run.id}`} className="inline-flex items-center gap-2 hover:underline">
                <StatusBadge status={workflow.last_run.state} />
                <RelativeTime value={workflow.last_run.created_at} className="text-muted-foreground" />
              </Link>
            ) : (
              <span className="text-muted-foreground">Never run</span>
            )}
          </dd>
        </div>
      </dl>

      <div className="mt-auto flex items-center gap-2">
        <Button variant="outline" size="sm" asChild>
          <Link href={`/workflows/${workflow.id}`}>
            <Pencil /> {can.write && !archived ? "Edit" : "View"}
          </Link>
        </Button>
        {can.write && !archived && (
          <Button size="sm" onClick={() => onRun(workflow)}>
            <Play /> Run
          </Button>
        )}
        {can.write && !archived && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="ml-auto size-8" aria-label={`More actions for ${workflow.name}`}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => router.push(`/workflows/new?from=${workflow.id}`)}>
                <GitFork /> Duplicate
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onSelect={() =>
                  archive.mutate(workflow.id, {
                    onSuccess: () => toast.success(`Archived ${workflow.name}`, { description: "Existing runs are kept." }),
                    onError: (error) => toast.error(errorMessage(error)),
                  })
                }
              >
                <Archive /> Archive
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {archived && <span className="ml-auto text-xs text-muted-foreground">Archived</span>}
      </div>
    </GlassCard>
  )
}

function RecentRuns() {
  const [stateFilter, setStateFilter] = useState<string>(ANY)
  const { data, isPending, isError, error, refetch } = useWorkflowRuns(undefined, stateFilter === ANY ? undefined : stateFilter)
  return (
    <section className="space-y-3">
      <SectionTitle
        actions={
          <Select value={stateFilter} onValueChange={setStateFilter}>
            <SelectTrigger className="h-8 w-40" aria-label="Filter runs by state">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All states</SelectItem>
              {RUN_STATES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s.charAt(0) + s.slice(1).toLowerCase()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      >
        Recent runs
      </SectionTitle>
      <GlassCard className="overflow-hidden">
        {isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : isPending ? (
          <RowsSkeleton rows={4} />
        ) : !data.items.length ? (
          <EmptyState title="No runs" description={stateFilter === ANY ? "Run a workflow to see it here." : "No runs in this state."} />
        ) : (
          <div className="glass-strong overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Run</TableHead>
                  <TableHead>Workflow</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Nodes</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead className="pr-4 text-right">Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="pl-4">
                      <Link href={`/workflows/runs/${run.id}`} title={run.id} className="font-mono text-xs text-primary hover:underline">
                        {run.id.slice(0, 8)}…{run.id.slice(-4)}
                      </Link>
                    </TableCell>
                    <TableCell>
                      {run.workflow_name ?? "—"}
                      {run.version != null && <span className="ml-1.5 font-mono text-xs text-muted-foreground">v{run.version}</span>}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={run.state} />
                    </TableCell>
                    <TableCell>
                      <NodeProgress counts={run.node_counts} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <RelativeTime value={run.started_at ?? run.created_at} />
                    </TableCell>
                    <TableCell className="pr-4 text-right tabular-nums">{runDuration(run)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </GlassCard>
    </section>
  )
}

export function WorkflowsPage() {
  const can = useCan()
  const [showArchived, setShowArchived] = useState(false)
  const { data, isPending, isError, error, refetch } = useWorkflows(showArchived)
  const [running, setRunning] = useState<Workflow | null>(null)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflows"
        description="Directed acyclic graphs of tasks. Every save creates a new immutable version; runs pin the version they started with."
        actions={
          <>
            <Label className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
              <Switch checked={showArchived} onCheckedChange={setShowArchived} /> Show archived
            </Label>
            {can.write && (
              <Button asChild>
                <Link href="/workflows/new">
                  <Plus /> New workflow
                </Link>
              </Button>
            )}
          </>
        }
      />

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-56 rounded-2xl" />
          ))}
        </div>
      ) : !data.length ? (
        <GlassCard>
          <EmptyState
            icon={<WorkflowIcon className="size-5" />}
            title="No workflows yet"
            description="Chain task types into a DAG: extract → transform → load, fan-out and fan-in."
            action={
              can.write ? (
                <Button asChild>
                  <Link href="/workflows/new">
                    <Plus /> Build your first workflow
                  </Link>
                </Button>
              ) : undefined
            }
          />
        </GlassCard>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((wf) => (
            <WorkflowCard key={wf.id} workflow={wf} onRun={setRunning} />
          ))}
        </div>
      )}

      <RecentRuns />
      <RunWorkflowDialog open={!!running} onOpenChange={(open) => !open && setRunning(null)} workflow={running} />
    </div>
  )
}
