"use client"

import { Background, BackgroundVariant, Controls, MarkerType, ReactFlow, ReactFlowProvider, type Edge } from "@xyflow/react"
import { AlertTriangle, ArrowLeft, Ban, SearchX } from "lucide-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useMemo } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import { EmptyState, ErrorState, GlassCard, JsonView, KeyValue, MonoId, RelativeTime } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { runNodeTypes, type RunStepNodeType } from "@/components/workflows/graph"
import { NodeProgress } from "@/components/workflows/workflows-page"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useCancelRun, useWorkflowRun } from "@/lib/api/hooks"
import type { RunDetail } from "@/lib/api/types"
import { layeredLayout, topologicalOrder } from "@/lib/dag"
import { dateTime, duration } from "@/lib/format"
import { useCan } from "@/lib/session"
import { useResolvedTheme } from "@/lib/theme"

const ACTIVE_RUN_STATES = new Set(["PENDING", "RUNNING"])

function toFlow(run: RunDetail): { nodes: RunStepNodeType[]; edges: Edge[] } {
  const keys = run.nodes.map((n) => n.key)
  const unplaced = keys.length > 1 && run.nodes.every((n) => n.position.x === 0 && n.position.y === 0)
  const positions = unplaced ? layeredLayout(keys, run.edges) : null
  const state = new Map(run.nodes.map((n) => [n.key, n.state]))
  return {
    nodes: run.nodes.map((n) => ({
      id: n.key,
      type: "runStep",
      position: positions?.[n.key] ?? n.position,
      draggable: false,
      connectable: false,
      data: {
        name: n.name,
        taskType: n.task_type,
        state: n.state,
        taskId: n.task_id,
        taskState: n.task_state,
        attempts: n.attempt_count,
        startedAt: n.started_at,
        finishedAt: n.finished_at,
      },
    })),
    edges: run.edges.map((e) => {
      const target = state.get(e.target)
      return {
        id: `${e.source}->${e.target}`,
        source: e.source,
        target: e.target,
        animated: target === "RUNNING",
        style: target === "SKIPPED" || target === "CANCELLED" ? { strokeDasharray: "4 4", opacity: 0.5 } : undefined,
        markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
      }
    }),
  }
}

function RunGraph({ run }: { run: RunDetail }) {
  const theme = useResolvedTheme()
  const { nodes, edges } = useMemo(() => toFlow(run), [run])
  return (
    <GlassCard className="h-[26rem] overflow-hidden p-0" aria-label="Run graph">
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={runNodeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          colorMode={theme}
          fitView
          fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
          minZoom={0.2}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--chart-grid)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
    </GlassCard>
  )
}

export function WorkflowRunPage() {
  const { runId } = useParams<{ runId: string }>()
  const { data: run, isPending, isError, error, refetch } = useWorkflowRun(runId)
  const cancel = useCancelRun()
  const can = useCan()
  // Steps in execution order (ties keep the API's order), which reads better than alphabetical.
  const orderedNodes = useMemo(() => {
    if (!run) return []
    const order = topologicalOrder(
      run.nodes.map((n) => n.key),
      run.edges,
    )
    const rank = new Map((order ?? []).map((key, i) => [key, i]))
    return [...run.nodes].sort((a, b) => (rank.get(a.key) ?? 0) - (rank.get(b.key) ?? 0))
  }, [run])

  if (isError) {
    if (error instanceof ApiError && [404, 422].includes(error.status)) {
      return (
        <EmptyState
          className="mt-16"
          icon={<SearchX className="size-5" />}
          title="Run not found"
          description="It may belong to another project, or the link is wrong."
          action={
            <Button asChild variant="outline">
              <Link href="/workflows">Back to workflows</Link>
            </Button>
          }
        />
      )
    }
    return <ErrorState error={error} onRetry={() => void refetch()} />
  }

  if (isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-24 w-full rounded-2xl" />
        <Skeleton className="h-[26rem] w-full rounded-2xl" />
      </div>
    )
  }

  const active = ACTIVE_RUN_STATES.has(run.state)
  const elapsed = run.started_at ? (run.finished_at ? duration(Date.parse(run.finished_at) - Date.parse(run.started_at)) : null) : null
  const builderHref = run.version != null ? `/workflows/${run.workflow_id}?version=${run.version}` : `/workflows/${run.workflow_id}`

  return (
    <div className="space-y-5">
      <Link href="/workflows" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Workflows
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
              <Link href={builderHref} className="hover:underline">
                {run.workflow_name ?? "Workflow"}
              </Link>
              {run.version != null && <span className="ml-2 font-mono text-base text-muted-foreground">v{run.version}</span>}
            </h1>
            <StatusBadge status={run.state} />
          </div>
          <MonoId value={run.id} full className="text-muted-foreground" />
        </div>
        {can.write && active && (
          <Confirm
            trigger={
              <Button variant="outline" disabled={cancel.isPending}>
                <Ban /> Cancel run
              </Button>
            }
            title="Cancel this run?"
            description="Queued steps are cancelled and running ones are asked to stop. Steps that already finished are not rolled back."
            confirmLabel="Cancel run"
            dismissLabel="Keep running"
            destructive
            onConfirm={() =>
              cancel.mutate(run.id, {
                onSuccess: () => toast.success("Run cancelled"),
                onError: (e) => toast.error(errorMessage(e)),
              })
            }
          />
        )}
      </header>

      {run.error && (
        <GlassCard className="flex items-start gap-3 p-4" role="alert">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
          <p className="text-sm break-words">{run.error}</p>
        </GlassCard>
      )}

      <GlassCard className="p-5">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <KeyValue label="Progress">
            <NodeProgress counts={run.node_counts} />
          </KeyValue>
          <KeyValue label="Started">{run.started_at ? dateTime(run.started_at) : "—"}</KeyValue>
          <KeyValue label="Finished">{run.finished_at ? dateTime(run.finished_at) : active ? "Still running" : "—"}</KeyValue>
          <KeyValue label="Duration">{elapsed ?? (active ? "Running" : "—")}</KeyValue>
        </dl>
      </GlassCard>

      <RunGraph run={run} />
      <p className="-mt-3 text-xs text-muted-foreground">Updates live. Click a step to open its task: logs, attempts and retry.</p>

      <GlassCard className="overflow-hidden">
        <div className="glass-strong overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Step</TableHead>
                <TableHead>Task type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Task</TableHead>
                <TableHead className="text-right">Attempts</TableHead>
                <TableHead>Started</TableHead>
                <TableHead className="pr-4 text-right">Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orderedNodes.map((n) => (
                <TableRow key={n.key}>
                  <TableCell className="pl-4">
                    <span className="font-medium">{n.name}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{n.key}</span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{n.task_type}</TableCell>
                  <TableCell>
                    <StatusBadge status={n.state} />
                  </TableCell>
                  <TableCell>
                    {n.task_id ? (
                      <Link href={`/tasks/${n.task_id}`} className="font-mono text-xs text-primary hover:underline" title={n.task_id}>
                        {n.task_id.slice(0, 8)}…{n.task_id.slice(-4)}
                      </Link>
                    ) : (
                      <span className="text-xs text-muted-foreground">not started</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{n.attempt_count ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    <RelativeTime value={n.started_at} />
                  </TableCell>
                  <TableCell className="pr-4 text-right tabular-nums">
                    {n.started_at && n.finished_at ? duration(Date.parse(n.finished_at) - Date.parse(n.started_at)) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </GlassCard>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Run input</h2>
        <JsonView value={run.input} maxHeight="16rem" />
      </section>
    </div>
  )
}
