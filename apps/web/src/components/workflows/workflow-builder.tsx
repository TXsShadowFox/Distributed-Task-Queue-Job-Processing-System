"use client"

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type OnConnectEnd,
} from "@xyflow/react"
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  History,
  Info,
  LayoutGrid,
  Loader2,
  MousePointerClick,
  PanelLeftClose,
  Play,
  Plus,
  Save,
  Search,
  SearchX,
  ShieldCheck,
  Trash2,
  Wand2,
} from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useEffectEvent, useMemo, useRef, useState, type DragEvent } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, GlassCard, RelativeTime } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { builderNodeTypes, type StepData, type StepNodeType } from "@/components/workflows/graph"
import { RunWorkflowDialog } from "@/components/workflows/run-workflow-dialog"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useCreateWorkflow, useSaveWorkflow, useTaskTypes, useValidateWorkflow, useWorkflow } from "@/lib/api/hooks"
import type { GraphIn, TaskType, WorkflowDetail } from "@/lib/api/types"
import { findPath, isValidNodeKey, layeredLayout, stages, uniqueKey, wouldCreateCycle } from "@/lib/dag"
import { titleCase } from "@/lib/format"
import { parseJsonObject, prettyJson } from "@/lib/json"
import { useCan } from "@/lib/session"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const DND_TYPE = "application/x-flowforge-task-type"
const QUEUE_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/
const NODE_WIDTH = 176
const NODE_GAP_X = 240 // matches layeredLayout's default column spacing

interface Problem {
  code: string
  message: string
  nodes: string[]
}

const edgeFor = (source: string, target: string): Edge => ({ id: `${source}->${target}`, source, target })

function intInRange(value: string, min: number, max: number): boolean {
  if (!value.trim()) return true
  const n = Number(value)
  return Number.isInteger(n) && n >= min && n <= max
}

/** Problems the browser can detect before asking the server (which re-validates everything on save). */
export function stepProblems(key: string, data: StepData, knownTypes: Set<string>): string[] {
  const out: string[] = []
  if (!isValidNodeKey(key)) out.push("Key must start with a letter and contain only letters, digits, - and _ (max 64).")
  if (!data.name.trim()) out.push("Name is required.")
  else if (data.name.length > 120) out.push("Name must be at most 120 characters.")
  if (!data.taskType) out.push("Choose a task type.")
  else if (!knownTypes.has(data.taskType)) out.push(`Unknown task type “${data.taskType}”: no worker has registered it.`)
  const payload = parseJsonObject(data.payloadText, "Payload")
  if (payload.error) out.push(payload.error)
  if (data.queue.trim() && !QUEUE_RE.test(data.queue.trim())) out.push("Queue names use lowercase letters, digits, - and _.")
  if (!intInRange(data.priority, 0, 9)) out.push("Priority must be a whole number from 0 to 9.")
  if (!intInRange(data.maxRetries, 0, 100)) out.push("Max retries must be a whole number from 0 to 100.")
  if (!intInRange(data.timeoutSeconds, 1, 86_400)) out.push("Timeout must be 1 to 86,400 seconds.")
  return out
}

const numberOrNull = (value: string) => (value.trim() === "" ? null : Number(value))

export function toGraphIn(nodes: StepNodeType[], edges: Edge[]): GraphIn {
  return {
    nodes: nodes.map((n) => ({
      key: n.id,
      name: n.data.name.trim(),
      task_type: n.data.taskType,
      payload: parseJsonObject(n.data.payloadText).value ?? {},
      queue: n.data.queue.trim() || null,
      priority: numberOrNull(n.data.priority),
      max_retries: numberOrNull(n.data.maxRetries),
      timeout_seconds: numberOrNull(n.data.timeoutSeconds),
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({ source: e.source, target: e.target })),
  }
}

function fromDetail(detail: WorkflowDetail): { nodes: StepNodeType[]; edges: Edge[] } {
  const edges = detail.graph.edges.map((e) => edgeFor(e.source, e.target))
  const keys = detail.graph.nodes.map((n) => n.key)
  // Graphs created through the API often have no positions; lay those out instead of stacking them at the origin.
  const unplaced = keys.length > 1 && detail.graph.nodes.every((n) => n.position.x === 0 && n.position.y === 0)
  const positions = unplaced ? layeredLayout(keys, edges) : null
  const nodes = detail.graph.nodes.map(
    (n): StepNodeType => ({
      id: n.key,
      type: "step",
      position: positions?.[n.key] ?? n.position,
      data: {
        name: n.name,
        taskType: n.task_type,
        payloadText: prettyJson(n.payload),
        queue: n.queue ?? "",
        priority: n.priority?.toString() ?? "",
        maxRetries: n.max_retries?.toString() ?? "",
        timeoutSeconds: n.timeout_seconds?.toString() ?? "",
      },
    }),
  )
  return { nodes, edges }
}

function serverProblemsFrom(details: unknown): Problem[] {
  if (!Array.isArray(details)) return []
  return (details as Record<string, unknown>[])
    .filter((d) => typeof d.code === "string")
    .map((d) => ({
      code: String(d.code),
      message: String(d.message ?? ""),
      nodes: Array.isArray(d.nodes) ? (d.nodes as string[]) : [],
    }))
}

// ------------------------------------------------------------------------------------------------ palette
function Palette({ types, onAdd }: { types: TaskType[]; onAdd: (taskType: string) => void }) {
  const [query, setQuery] = useState("")
  const needle = query.trim().toLowerCase()
  const shown = types.filter((t) => !needle || t.name.includes(needle) || t.description.toLowerCase().includes(needle))
  return (
    <GlassCard id="task-palette" className="flex min-h-0 flex-col gap-2 p-3">
      <div className="px-1">
        <h2 className="text-sm font-medium">Task types</h2>
        <p className="text-xs text-muted-foreground">Drag onto the canvas, or click to add.</p>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter…"
          aria-label="Filter task types"
          className="h-8 pl-8 text-xs"
        />
      </div>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto max-lg:max-h-64">
        {shown.map((t) => (
          <li key={t.name}>
            <button
              type="button"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(DND_TYPE, t.name)
                e.dataTransfer.effectAllowed = "move"
              }}
              onClick={() => onAdd(t.name)}
              className="w-full cursor-grab rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-muted/50 active:cursor-grabbing"
            >
              <span className="block truncate font-mono text-xs text-primary">{t.name}</span>
              <span className="line-clamp-2 text-[11px] text-muted-foreground">{t.description}</span>
            </button>
          </li>
        ))}
        {!shown.length && <li className="px-2 py-4 text-center text-xs text-muted-foreground">No task type matches.</li>}
      </ul>
    </GlassCard>
  )
}

// ------------------------------------------------------------------------------------------------ inspector
function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={htmlFor} className="text-xs">
        {label}
      </Label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  )
}

function StepInspector({
  node,
  types,
  readOnly,
  problems,
  upstream,
  downstream,
  onChange,
  onRename,
  onDelete,
  onFocus,
}: {
  node: StepNodeType
  types: TaskType[]
  readOnly: boolean
  problems: string[]
  upstream: string[]
  downstream: string[]
  onChange: (patch: Partial<StepData>) => void
  onRename: (key: string) => string | null
  onDelete: () => void
  onFocus: (key: string) => void
}) {
  const [keyDraft, setKeyDraft] = useState(node.id)
  const [keyError, setKeyError] = useState<string | null>(null)
  const type = types.find((t) => t.name === node.data.taskType)
  const payload = parseJsonObject(node.data.payloadText, "Payload")
  const commitKey = () => {
    const error = onRename(keyDraft.trim())
    setKeyError(error)
  }
  const changeType = (name: string) => {
    const next = types.find((t) => t.name === name)
    const untouched = !node.data.payloadText.trim() || node.data.payloadText === prettyJson(type?.example_payload ?? {})
    onChange({ taskType: name, ...(untouched && next ? { payloadText: prettyJson(next.example_payload) } : {}) })
  }
  const id = (field: string) => `step-${field}`

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Step</h2>
        {!readOnly && (
          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={onDelete}>
            <Trash2 /> Delete
          </Button>
        )}
      </div>

      {problems.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive" role="alert">
          {problems.map((p) => (
            <li key={p} className="flex gap-1.5">
              <AlertCircle className="mt-0.5 size-3 shrink-0" aria-hidden />
              {p}
            </li>
          ))}
        </ul>
      )}

      <fieldset disabled={readOnly} className="space-y-3">
        <Field label="Name" htmlFor={id("name")}>
          <Input id={id("name")} value={node.data.name} onChange={(e) => onChange({ name: e.target.value })} className="h-8" />
        </Field>
        <Field label="Key" htmlFor={id("key")} hint={keyError ?? "Unique within the workflow; used in edges and upstream results."}>
          <Input
            id={id("key")}
            value={keyDraft}
            onChange={(e) => setKeyDraft(e.target.value)}
            onBlur={commitKey}
            onKeyDown={(e) => e.key === "Enter" && commitKey()}
            aria-invalid={!!keyError}
            className="h-8 font-mono text-xs"
          />
        </Field>
        <Field label="Task type" htmlFor={id("type")} hint={type?.description}>
          <Select value={node.data.taskType} onValueChange={changeType} disabled={readOnly}>
            <SelectTrigger id={id("type")} className="h-8 w-full">
              <SelectValue placeholder="Choose a task type" />
            </SelectTrigger>
            <SelectContent>
              {types.map((t) => (
                <SelectItem key={t.name} value={t.name}>
                  <span className="font-mono text-xs">{t.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label htmlFor={id("payload")} className="text-xs">
              Payload (JSON)
            </Label>
            {type && !readOnly && (
              <Button type="button" variant="ghost" size="xs" onClick={() => onChange({ payloadText: prettyJson(type.example_payload) })}>
                <Wand2 /> Example
              </Button>
            )}
          </div>
          <Textarea
            id={id("payload")}
            value={node.data.payloadText}
            onChange={(e) => onChange({ payloadText: e.target.value })}
            rows={6}
            spellCheck={false}
            aria-invalid={!!payload.error}
            className="font-mono text-xs"
          />
          <p className="text-[11px] text-muted-foreground">
            Validated against the task type&apos;s schema on save. At run time the handler also gets the run input and its
            upstream results.
          </p>
        </div>
        <details className="group rounded-lg border border-border p-2.5 [&_summary::-webkit-details-marker]:hidden">
          <summary className="cursor-pointer text-xs font-medium select-none">Execution overrides</summary>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field label="Queue" htmlFor={id("queue")}>
              <Input id={id("queue")} value={node.data.queue} onChange={(e) => onChange({ queue: e.target.value })} placeholder={type?.queue ?? "default"} className="h-8 text-xs" />
            </Field>
            <Field label="Priority (0–9)" htmlFor={id("priority")}>
              <Input id={id("priority")} inputMode="numeric" value={node.data.priority} onChange={(e) => onChange({ priority: e.target.value })} placeholder={String(type?.default_priority ?? 5)} className="h-8 text-xs" />
            </Field>
            <Field label="Max retries" htmlFor={id("retries")}>
              <Input id={id("retries")} inputMode="numeric" value={node.data.maxRetries} onChange={(e) => onChange({ maxRetries: e.target.value })} placeholder={String(type?.default_max_retries ?? 3)} className="h-8 text-xs" />
            </Field>
            <Field label="Timeout (s)" htmlFor={id("timeout")}>
              <Input id={id("timeout")} inputMode="numeric" value={node.data.timeoutSeconds} onChange={(e) => onChange({ timeoutSeconds: e.target.value })} placeholder={String(type?.default_timeout_seconds ?? 300)} className="h-8 text-xs" />
            </Field>
          </div>
        </details>
      </fieldset>

      <div className="space-y-2 text-xs">
        <DependencyList label="Runs after" keys={upstream} empty="Nothing: starts when the run starts." onFocus={onFocus} />
        <DependencyList label="Then starts" keys={downstream} empty="Nothing: this is a final step." onFocus={onFocus} />
      </div>
    </div>
  )
}

function DependencyList({ label, keys, empty, onFocus }: { label: string; keys: string[]; empty: string; onFocus: (key: string) => void }) {
  return (
    <div>
      <p className="text-muted-foreground">{label}</p>
      {keys.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {keys.map((k) => (
            <button key={k} type="button" onClick={() => onFocus(k)} className="rounded-md bg-muted px-1.5 py-0.5 font-mono hover:bg-muted/70">
              {k}
            </button>
          ))}
        </div>
      ) : (
        <p className="mt-0.5">{empty}</p>
      )}
    </div>
  )
}

function OverviewPanel({
  nodes,
  edges,
  problems,
  detail,
  viewingVersion,
  onFocus,
}: {
  nodes: StepNodeType[]
  edges: Edge[]
  problems: Problem[]
  detail: WorkflowDetail | null
  viewingVersion: number | null
  onFocus: (key: string) => void
}) {
  const plan = useMemo(() => stages(nodes.map((n) => n.id), edges), [nodes, edges])
  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <h2 className="text-sm font-medium">Problems</h2>
        {problems.length ? (
          <ul className="space-y-1.5">
            {problems.map((p, i) => (
              <li key={`${p.code}-${i}`} className="flex gap-2 text-xs">
                <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />
                <span>
                  {p.nodes.map((k) => (
                    <button key={k} type="button" onClick={() => onFocus(k)} className="mr-1 rounded bg-muted px-1 font-mono hover:bg-muted/70">
                      {k}
                    </button>
                  ))}
                  {p.message}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CheckCircle2 className="size-3.5 text-success" aria-hidden /> {nodes.length ? "No problems detected." : "Add a step to begin."}
          </p>
        )}
      </section>

      {plan && plan.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Execution plan</h2>
          <ol className="space-y-1.5 text-xs">
            {plan.map((stage, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-14 shrink-0 text-muted-foreground tabular-nums">Stage {i + 1}</span>
                <span className="flex flex-wrap gap-1">
                  {stage.map((k) => (
                    <button key={k} type="button" onClick={() => onFocus(k)} className="rounded bg-muted px-1.5 py-0.5 font-mono hover:bg-muted/70">
                      {k}
                    </button>
                  ))}
                </span>
              </li>
            ))}
          </ol>
          <p className="text-[11px] text-muted-foreground">Steps in the same stage can run in parallel.</p>
        </section>
      )}

      {detail && detail.versions.length > 0 && (
        <section className="space-y-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium">
            <History className="size-4" aria-hidden /> Versions
          </h2>
          <ul className="space-y-1 text-xs">
            {[...detail.versions].reverse().map((v) => {
              const current = (viewingVersion ?? detail.latest_version) === v.version
              return (
                <li key={v.version} className="flex items-center justify-between gap-2">
                  <Link
                    href={v.version === detail.latest_version ? `/workflows/${detail.id}` : `/workflows/${detail.id}?version=${v.version}`}
                    className={cn("font-mono hover:underline", current && "font-semibold text-primary")}
                    aria-current={current ? "page" : undefined}
                  >
                    v{v.version}
                    {v.version === detail.latest_version && " · latest"}
                  </Link>
                  <RelativeTime value={v.created_at} className="text-muted-foreground" />
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <section className="space-y-1.5 rounded-lg bg-muted/40 p-3 text-[11px] text-muted-foreground">
        <p className="flex items-center gap-1.5 font-medium text-foreground">
          <Info className="size-3.5" aria-hidden /> How it works
        </p>
        <p>Drag from a step&apos;s right handle to another step&apos;s left handle: the second runs after the first succeeds.</p>
        <p>Connections that would create a cycle are rejected. Select a step or connection and press Delete to remove it.</p>
        <p>Ctrl/⌘ + S saves a new version.</p>
      </section>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ builder
function Builder({
  mode,
  detail,
  types,
  requestedVersion,
  onReload,
}: {
  mode: "new" | "edit"
  detail: WorkflowDetail | null
  types: TaskType[]
  requestedVersion?: number
  onReload: () => void
}) {
  const can = useCan()
  const router = useRouter()
  const theme = useResolvedTheme()
  const flow = useReactFlow<StepNodeType, Edge>()
  const wrapper = useRef<HTMLElement>(null)

  const [initial] = useState(() => (detail ? fromDetail(detail) : { nodes: [], edges: [] }))
  const [nodes, setNodes, applyNodeChanges] = useNodesState<StepNodeType>(initial.nodes)
  const [edges, setEdges, applyEdgeChanges] = useEdgesState<Edge>(initial.edges)
  const [name, setName] = useState(() => (mode === "new" ? (detail ? `Copy of ${detail.name}`.slice(0, 120) : "") : (detail?.name ?? "")))
  const [description, setDescription] = useState(() => detail?.description ?? "")
  const [dirty, setDirty] = useState(mode === "new" && !!detail)
  const [baseVersion, setBaseVersion] = useState<number | null>(mode === "edit" ? (detail?.latest_version ?? null) : null)
  const [serverProblems, setServerProblems] = useState<Problem[]>([])
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const [runOpen, setRunOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(() => initial.nodes.length === 0)

  const workflowId = mode === "edit" ? (detail?.id ?? null) : null
  const viewingOld = mode === "edit" && !!detail && requestedVersion != null && requestedVersion !== detail.latest_version
  const archived = mode === "edit" && !!detail?.archived_at
  const readOnly = !can.write || viewingOld || archived
  const canRestore = viewingOld && can.write && !archived

  const create = useCreateWorkflow()
  const save = useSaveWorkflow(workflowId ?? "")
  const validate = useValidateWorkflow()
  const saving = create.isPending || save.isPending

  const touch = () => {
    setDirty(true)
    setServerProblems([])
  }

  const onNodesChange = (changes: NodeChange<StepNodeType>[]) => {
    applyNodeChanges(changes)
    if (changes.some((c) => c.type === "position" || c.type === "remove" || c.type === "add" || c.type === "replace")) touch()
  }
  const onEdgesChange = (changes: EdgeChange<Edge>[]) => {
    applyEdgeChanges(changes)
    if (changes.some((c) => c.type === "remove" || c.type === "add" || c.type === "replace")) touch()
  }

  const knownTypes = useMemo(() => new Set(types.map((t) => t.name)), [types])
  const clientProblems = useMemo(
    () => nodes.flatMap((n) => stepProblems(n.id, n.data, knownTypes).map((message) => ({ code: "CLIENT", message, nodes: [n.id] }))),
    [nodes, knownTypes],
  )
  const problems = useMemo(() => [...clientProblems, ...serverProblems], [clientProblems, serverProblems])
  const problemsByNode = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const p of problems) for (const key of p.nodes) map.set(key, [...(map.get(key) ?? []), p.message])
    return map
  }, [problems])
  const displayNodes = useMemo(
    () => nodes.map((n) => (problemsByNode.has(n.id) ? { ...n, data: { ...n.data, problems: problemsByNode.get(n.id) } } : n)),
    [nodes, problemsByNode],
  )

  const selectedNode = nodes.find((n) => n.selected) ?? null
  const selectedEdge = selectedNode ? null : (edges.find((e) => e.selected) ?? null)

  const focusNode = (key: string) => {
    setNodes((ns) => ns.map((n) => (n.selected === (n.id === key) ? n : { ...n, selected: n.id === key })))
    void flow.fitView({ nodes: [{ id: key }], duration: 300, maxZoom: 1.2 })
  }

  /** Where a clicked (not dropped) step goes: right of the rightmost step, so clicking types in order builds a chain. */
  const nextPosition = () => {
    if (nodes.length) {
      const last = nodes.reduce((a, b) => (b.position.x > a.position.x ? b : a))
      return { x: last.position.x + NODE_GAP_X, y: last.position.y }
    }
    const rect = wrapper.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    const p = flow.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
    return { x: p.x - NODE_WIDTH / 2, y: p.y - 32 }
  }

  const addStep = (taskType: string, position?: { x: number; y: number }) => {
    if (readOnly) return
    const type = types.find((t) => t.name === taskType)
    const key = uniqueKey(taskType.split(".").pop() ?? "step", new Set(nodes.map((n) => n.id)))
    const step: StepNodeType = {
      id: key,
      type: "step",
      position: position ?? nextPosition(),
      selected: true,
      data: {
        name: titleCase(key.replace(/[-_]+/g, " ")),
        taskType,
        payloadText: prettyJson(type?.example_payload ?? {}),
        queue: "",
        priority: "",
        maxRetries: "",
        timeoutSeconds: "",
      },
    }
    setNodes((ns) => [...ns.map((n) => (n.selected ? { ...n, selected: false } : n)), step])
    touch()
    // A clicked step may land outside the view; keep the whole graph visible.
    if (!position) requestAnimationFrame(() => void flow.fitView({ padding: 0.25, maxZoom: 1.1, duration: 200 }))
  }

  const updateStep = (key: string, patch: Partial<StepData>) => {
    setNodes((ns) => ns.map((n) => (n.id === key ? { ...n, data: { ...n.data, ...patch } } : n)))
    touch()
  }

  const renameStep = (from: string, to: string): string | null => {
    if (to === from) return null
    if (!isValidNodeKey(to)) return "Start with a letter; use letters, digits, - and _ (max 64)."
    if (nodes.some((n) => n.id === to)) return "Another step already uses this key."
    setNodes((ns) => ns.map((n) => (n.id === from ? { ...n, id: to } : n)))
    setEdges((es) =>
      es.map((e) =>
        e.source === from || e.target === from
          ? edgeFor(e.source === from ? to : e.source, e.target === from ? to : e.target)
          : e,
      ),
    )
    touch()
    return null
  }

  const isValidConnection = (c: Connection | Edge) =>
    !!c.source &&
    !!c.target &&
    c.source !== c.target &&
    !edges.some((e) => e.source === c.source && e.target === c.target) &&
    !wouldCreateCycle(edges, c.source, c.target)

  const onConnect = (c: Connection) => {
    if (!isValidConnection(c)) return
    setEdges((es) => addEdge(edgeFor(c.source, c.target), es))
    touch()
  }

  // Explain why a dropped connection was refused instead of silently ignoring it.
  const onConnectEnd: OnConnectEnd = (_event, state) => {
    if (state.isValid !== false || !state.fromNode || !state.toNode) return
    let source = state.fromNode.id
    let target = state.toNode.id
    if (state.fromHandle?.type === "target") [source, target] = [target, source]
    if (source === target) toast.error("A step cannot depend on itself")
    else if (edges.some((e) => e.source === source && e.target === target)) toast.info("These steps are already connected")
    else {
      const path = findPath(edges, target, source)
      toast.error("That connection would create a cycle", { description: path ? [source, ...path].join(" → ") : undefined })
    }
  }

  const onDragOver = (e: DragEvent) => {
    if (readOnly || !e.dataTransfer.types.includes(DND_TYPE)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = "move"
  }
  const onDrop = (e: DragEvent) => {
    const taskType = e.dataTransfer.getData(DND_TYPE)
    if (!taskType) return
    e.preventDefault()
    const p = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY })
    addStep(taskType, { x: p.x - NODE_WIDTH / 2, y: p.y - 32 })
  }

  const arrange = () => {
    const positions = layeredLayout(
      nodes.map((n) => n.id),
      edges,
    )
    setNodes((ns) => ns.map((n) => ({ ...n, position: positions[n.id] ?? n.position })))
    touch()
    requestAnimationFrame(() => void flow.fitView({ padding: 0.2, duration: 300, maxZoom: 1.1 }))
  }

  const runValidation = () => {
    if (!nodes.length) {
      toast.error("Add at least one step first")
      return
    }
    validate.mutate(toGraphIn(nodes, edges), {
      onSuccess: (result) => {
        setServerProblems(serverProblemsFrom(result.errors))
        if (result.valid && !clientProblems.length) {
          toast.success("The workflow is valid", { description: `Order: ${result.order.join(" → ")}` })
        } else {
          toast.error(`${result.errors.length + clientProblems.length} problem(s) found`, { description: "See the Problems panel." })
        }
      },
      onError: (error) => toast.error(errorMessage(error)),
    })
  }

  const onSaveError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      const latest = (error.details as { latest_version?: number } | null)?.latest_version
      if (typeof latest === "number") {
        setConflictVersion(latest)
        return
      }
    }
    if (error instanceof ApiError && error.status === 422) {
      const list = serverProblemsFrom(error.details)
      if (list.length) {
        setServerProblems(list)
        toast.error("The server rejected the workflow", { description: `${list.length} problem(s): see the Problems panel.` })
        return
      }
    }
    toast.error(errorMessage(error))
  }

  const persist = () => {
    if (saving || (readOnly && !canRestore)) return
    if (!name.trim()) {
      toast.error("Give the workflow a name")
      document.getElementById("workflow-name")?.focus()
      return
    }
    if (!nodes.length) {
      toast.error("Add at least one step")
      return
    }
    if (clientProblems.length) {
      toast.error("Fix the highlighted problems first", { description: clientProblems[0].message })
      return
    }
    const graph = toGraphIn(nodes, edges)
    if (mode === "new") {
      create.mutate(
        { name: name.trim(), description: description.trim() || null, graph },
        {
          onSuccess: (wf) => {
            setDirty(false)
            toast.success(`Created “${wf.name}”`)
            router.replace(`/workflows/${wf.id}`)
          },
          onError: onSaveError,
        },
      )
      return
    }
    save.mutate(
      {
        graph,
        name: name.trim(),
        description: description.trim(),
        expected_version: (viewingOld ? detail?.latest_version : baseVersion) ?? undefined,
      },
      {
        onSuccess: (wf) => {
          setDirty(false)
          setBaseVersion(wf.latest_version)
          toast.success(viewingOld ? `Restored as v${wf.latest_version}` : `Saved v${wf.latest_version}`)
          if (viewingOld) router.replace(`/workflows/${wf.id}`)
        },
        onError: onSaveError,
      },
    )
  }

  const onSaveShortcut = useEffectEvent((event: KeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault()
      persist()
    }
  })
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => onSaveShortcut(event)
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [dirty])

  const upstream = selectedNode ? edges.filter((e) => e.target === selectedNode.id).map((e) => e.source) : []
  const downstream = selectedNode ? edges.filter((e) => e.source === selectedNode.id).map((e) => e.target) : []

  return (
    <div className="flex flex-col gap-3 lg:h-[calc(100dvh-7.5rem)]">
      {/* The visible title is an editable field, so the page heading is for assistive technology only. */}
      <h1 className="sr-only">{mode === "new" ? "New workflow" : `Workflow builder: ${name || "untitled"}`}</h1>
      <div className="flex flex-wrap items-start gap-3">
        <Button variant="ghost" size="icon" className="mt-0.5" asChild>
          <Link href="/workflows" aria-label="Back to workflows">
            <ArrowLeft />
          </Link>
        </Button>
        <div className="min-w-56 flex-1">
          <Input
            id="workflow-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              setDirty(true)
            }}
            readOnly={readOnly}
            placeholder="Untitled workflow"
            aria-label="Workflow name"
            maxLength={120}
            className="h-9 border-transparent bg-transparent px-1 text-lg font-semibold shadow-none hover:border-input focus-visible:border-input dark:bg-transparent"
          />
          <Input
            value={description}
            onChange={(e) => {
              setDescription(e.target.value)
              setDirty(true)
            }}
            readOnly={readOnly}
            placeholder={readOnly ? "No description" : "Add a description"}
            aria-label="Workflow description"
            maxLength={2000}
            className="h-7 border-transparent bg-transparent px-1 text-sm text-muted-foreground shadow-none hover:border-input focus-visible:border-input dark:bg-transparent"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground" role="status">
            {dirty ? "Unsaved changes" : baseVersion ? `Saved · v${baseVersion}` : ""}
          </span>
          {!readOnly && (
            <Button variant="outline" onClick={() => setPaletteOpen((v) => !v)} aria-expanded={paletteOpen} aria-controls="task-palette">
              {paletteOpen ? <PanelLeftClose /> : <Plus />} {paletteOpen ? "Hide task types" : "Add step"}
            </Button>
          )}
          {!readOnly && (
            <Button variant="outline" onClick={arrange} disabled={nodes.length < 2}>
              <LayoutGrid /> Arrange
            </Button>
          )}
          <Button variant="outline" onClick={runValidation} disabled={!nodes.length || validate.isPending}>
            {validate.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />} Validate
          </Button>
          {(!readOnly || canRestore) && (
            <Button onClick={persist} disabled={saving || (!dirty && mode === "edit" && !viewingOld)}>
              {saving ? <Loader2 className="animate-spin" /> : <Save />}
              {mode === "new" ? "Create workflow" : viewingOld ? `Restore v${requestedVersion}` : "Save version"}
            </Button>
          )}
          {mode === "edit" && can.write && !archived && !viewingOld && (
            <Button variant="secondary" onClick={() => setRunOpen(true)} disabled={dirty} title={dirty ? "Save your changes before running" : undefined}>
              <Play /> Run
            </Button>
          )}
        </div>
      </div>

      {viewingOld && detail && (
        <Banner>
          You are viewing <strong>v{requestedVersion}</strong>, read-only. The latest version is v{detail.latest_version}.{" "}
          <Link href={`/workflows/${detail.id}`} className="font-medium text-primary hover:underline">
            Open the latest
          </Link>
          {canRestore && " or restore this one as a new version."}
        </Banner>
      )}
      {archived && <Banner>This workflow is archived. Its definition and runs are kept, but it cannot be edited or run.</Banner>}
      {!can.write && !archived && <Banner>You have read-only access to this project.</Banner>}
      {conflictVersion != null && (
        <Banner tone="danger">
          Someone saved v{conflictVersion} while you were editing, so your save was refused to avoid overwriting their work.{" "}
          <button type="button" onClick={onReload} className="font-medium underline">
            Load v{conflictVersion}
          </button>{" "}
          (discards your unsaved changes).
        </Banner>
      )}

      <div
        className={cn(
          "grid min-h-0 flex-1 gap-3",
          readOnly || !paletteOpen ? "lg:grid-cols-[minmax(0,1fr)_18rem]" : "lg:grid-cols-[13rem_minmax(0,1fr)_18rem]",
        )}
      >
        {!readOnly && paletteOpen && <Palette types={types} onAdd={(t) => addStep(t)} />}

        <GlassCard ref={wrapper} className="relative h-[65vh] overflow-hidden p-0 lg:h-auto" onDragOver={onDragOver} onDrop={onDrop} aria-label="Workflow canvas">
          <ReactFlow<StepNodeType, Edge>
            nodes={displayNodes}
            edges={edges}
            nodeTypes={builderNodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd}
            isValidConnection={isValidConnection}
            nodesDraggable={!readOnly}
            nodesConnectable={!readOnly}
            deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
            defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 } }}
            colorMode={theme}
            fitView
            fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
            minZoom={0.2}
            maxZoom={2}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--chart-grid)" />
            <Controls showInteractive={false} />
            {nodes.length > 0 && (
              <MiniMap pannable zoomable className="max-xl:!hidden" nodeColor={theme === "dark" ? "#0fa5c7" : "#0b7f9e"} maskColor="rgb(0 0 0 / 0.12)" />
            )}
          </ReactFlow>
          {!nodes.length && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
              <div className="max-w-xs text-center text-sm text-muted-foreground">
                <MousePointerClick className="mx-auto mb-2 size-6" aria-hidden />
                {readOnly ? "This version has no steps." : "Drag a task type from the list onto the canvas, or click one to add it."}
              </div>
            </div>
          )}
        </GlassCard>

        <GlassCard className="min-h-0 overflow-y-auto p-4">
          {selectedNode ? (
            <StepInspector
              key={selectedNode.id}
              node={selectedNode}
              types={types}
              readOnly={readOnly}
              problems={problemsByNode.get(selectedNode.id) ?? []}
              upstream={upstream}
              downstream={downstream}
              onChange={(patch) => updateStep(selectedNode.id, patch)}
              onRename={(key) => renameStep(selectedNode.id, key)}
              onDelete={() => void flow.deleteElements({ nodes: [{ id: selectedNode.id }] })}
              onFocus={focusNode}
            />
          ) : selectedEdge ? (
            <div className="space-y-3">
              <h2 className="text-sm font-medium">Connection</h2>
              <p className="flex items-center gap-2 font-mono text-xs">
                {selectedEdge.source} <ArrowRight className="size-3.5" aria-hidden /> {selectedEdge.target}
              </p>
              <p className="text-xs text-muted-foreground">
                <span className="font-mono">{selectedEdge.target}</span> starts only after <span className="font-mono">{selectedEdge.source}</span>{" "}
                succeeds.
              </p>
              {!readOnly && (
                <Button variant="outline" size="sm" onClick={() => void flow.deleteElements({ edges: [{ id: selectedEdge.id }] })}>
                  <Trash2 /> Remove connection
                </Button>
              )}
            </div>
          ) : (
            <OverviewPanel
              nodes={nodes}
              edges={edges}
              problems={problems}
              detail={mode === "edit" ? detail : null}
              viewingVersion={requestedVersion ?? null}
              onFocus={focusNode}
            />
          )}
        </GlassCard>
      </div>

      {mode === "edit" && detail && (
        <RunWorkflowDialog
          open={runOpen}
          onOpenChange={setRunOpen}
          workflow={{
            id: detail.id,
            name: name || detail.name,
            latest_version: baseVersion ?? detail.latest_version,
            versions: detail.versions.map((v) => v.version),
          }}
        />
      )}
    </div>
  )
}

function Banner({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "danger" }) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2 rounded-xl border px-3 py-2 text-sm",
        tone === "danger" ? "border-destructive/40 bg-destructive/10" : "border-info/30 bg-info/10",
      )}
    >
      {tone === "danger" ? <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden /> : <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />}
      <p>{children}</p>
    </div>
  )
}

function BuilderSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-10 w-80" />
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <Skeleton className="h-[60vh] rounded-2xl" />
        <Skeleton className="h-[60vh] rounded-2xl" />
      </div>
    </div>
  )
}

/** /workflows/new (optionally ?from=<id> to duplicate) and /workflows/<id> (optionally ?version=<n>). */
export function WorkflowBuilderRoute({ mode }: { mode: "new" | "edit" }) {
  const params = useParams<{ workflowId?: string }>()
  const search = useSearchParams()
  const sourceId = mode === "new" ? search.get("from") : null
  const id = mode === "edit" ? (params.workflowId ?? "") : (sourceId ?? "")
  const versionParam = mode === "edit" ? search.get("version") : null
  const requestedVersion = versionParam && /^\d+$/.test(versionParam) ? Number(versionParam) : undefined

  const types = useTaskTypes()
  const detail = useWorkflow(id, requestedVersion)
  const [reloads, setReloads] = useState(0)

  if (types.isError) return <ErrorState error={types.error} onRetry={() => void types.refetch()} />
  if (id && detail.isError) {
    if (detail.error instanceof ApiError && [404, 422].includes(detail.error.status)) {
      return (
        <EmptyState
          className="mt-16"
          icon={<SearchX className="size-5" />}
          title={requestedVersion ? `Version ${requestedVersion} not found` : "Workflow not found"}
          description="It may belong to another project, or the link is wrong."
          action={
            <Button asChild variant="outline">
              <Link href="/workflows">Back to workflows</Link>
            </Button>
          }
        />
      )
    }
    return <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
  }
  if (types.isPending || (id && detail.isPending)) return <BuilderSkeleton />

  return (
    <ReactFlowProvider>
      <Builder
        key={`${mode}:${id}:${requestedVersion ?? "latest"}:${reloads}`}
        mode={mode}
        detail={id ? (detail.data ?? null) : null}
        types={types.data}
        requestedVersion={requestedVersion}
        onReload={() => void detail.refetch().then(() => setReloads((n) => n + 1))}
      />
    </ReactFlowProvider>
  )
}
