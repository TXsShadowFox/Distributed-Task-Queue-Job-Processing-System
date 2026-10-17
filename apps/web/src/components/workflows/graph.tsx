"use client"

import "@xyflow/react/dist/style.css"

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react"
import { AlertCircle, Clock, RotateCcw } from "lucide-react"
import Link from "next/link"

import { StatusBadge } from "@/components/common/status-badge"
import { duration } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Editable step in the workflow builder. Its React Flow id is the node key. */
export interface StepData extends Record<string, unknown> {
  name: string
  taskType: string
  payloadText: string
  queue: string
  priority: string
  maxRetries: string
  timeoutSeconds: string
  problems?: string[]
}
export type StepNodeType = Node<StepData, "step">

/** One node of a workflow run, with the state of the task that executes it. */
export interface RunStepData extends Record<string, unknown> {
  name: string
  taskType: string
  state: string
  taskId: string | null
  taskState: string | null
  attempts: number | null
  startedAt: string | null
  finishedAt: string | null
}
export type RunStepNodeType = Node<RunStepData, "runStep">

const handleClass = "!size-2.5 !border-2"

export function StepNode({ id, data, selected }: NodeProps<StepNodeType>) {
  const problems = data.problems ?? []
  return (
    <div
      className={cn(
        "glass-strong w-44 rounded-xl border border-border px-3 py-2.5 text-left shadow-lg shadow-black/10 transition-shadow",
        selected && "ring-2 ring-primary",
        problems.length > 0 && "ring-2 ring-destructive",
      )}
    >
      <Handle type="target" position={Position.Left} className={handleClass} />
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-medium">{data.name || id}</p>
        {problems.length > 0 && (
          <AlertCircle className="size-4 shrink-0 text-destructive" aria-label={`${problems.length} problem(s)`} />
        )}
      </div>
      <p className="truncate font-mono text-[11px] text-primary">{data.taskType || "no task type"}</p>
      <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">key: {id}</p>
      <Handle type="source" position={Position.Right} className={handleClass} />
    </div>
  )
}

export function RunStepNode({ id, data }: NodeProps<RunStepNodeType>) {
  const elapsed =
    data.startedAt && data.finishedAt ? duration(Date.parse(data.finishedAt) - Date.parse(data.startedAt)) : null
  const body = (
    <>
      <Handle type="target" position={Position.Left} className={handleClass} isConnectable={false} />
      <p className="truncate text-sm font-medium">{data.name || id}</p>
      <p className="truncate font-mono text-[11px] text-muted-foreground">{data.taskType}</p>
      <StatusBadge status={data.state} className="mt-1.5" />
      {(elapsed || (data.attempts ?? 0) > 1) && (
        <div className="mt-1.5 flex items-center gap-3 text-[11px] text-muted-foreground">
          {data.attempts != null && data.attempts > 1 && (
            <span className="inline-flex items-center gap-1">
              <RotateCcw className="size-3" aria-hidden /> {data.attempts} attempts
            </span>
          )}
          {elapsed && (
            <span className="inline-flex items-center gap-1">
              <Clock className="size-3" aria-hidden /> {elapsed}
            </span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} className={handleClass} isConnectable={false} />
    </>
  )
  const className = cn(
    "glass-strong block w-44 rounded-xl border border-border px-3 py-2.5 text-left shadow-lg shadow-black/10",
    data.state === "RUNNING" && "ring-2 ring-indigo-400/60",
    data.state === "FAILED" && "ring-2 ring-destructive/70",
  )
  return data.taskId ? (
    <Link href={`/tasks/${data.taskId}`} className={cn(className, "hover:border-primary/60")} title="Open the node's task">
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  )
}

export const builderNodeTypes = { step: StepNode }
export const runNodeTypes = { runStep: RunStepNode }
