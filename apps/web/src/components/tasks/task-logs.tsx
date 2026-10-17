"use client"

import { ArrowDownToLine, Download, Radio, ScrollText, Search } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { EmptyState, ErrorState, RowsSkeleton } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useTaskLogs } from "@/lib/api/hooks"
import type { TaskLog } from "@/lib/api/types"
import { useSession } from "@/lib/session"
import { runEventStream, type StreamStatus } from "@/lib/sse"
import { cn } from "@/lib/utils"

const LEVEL_RANK: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }
const LEVEL_CLASS: Record<string, string> = {
  DEBUG: "text-muted-foreground",
  INFO: "text-info",
  WARNING: "text-warning",
  ERROR: "text-destructive",
  CRITICAL: "text-destructive font-semibold",
}

/** Merge log lines by id (the stream re-sends its backlog on reconnect) and keep them in id order. */
export function mergeLogLines(...sources: (readonly TaskLog[] | undefined)[]): TaskLog[] {
  const byId = new Map<number, TaskLog>()
  for (const source of sources) for (const line of source ?? []) byId.set(line.id, line)
  return [...byId.values()].sort((a, b) => a.id - b.id)
}

function formatFields(fields: TaskLog["fields"]): string {
  if (!fields) return ""
  return Object.entries(fields)
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" ")
}

function timeOfDay(iso: string): string {
  const d = new Date(iso)
  return `${d.toLocaleTimeString(undefined, { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`
}

/** Tail of a running task's logs over SSE; settles to the stored log once the task reaches a terminal state. */
function useLiveLogLines(taskId: string, live: boolean) {
  const projectId = useSession((s) => s.projectId)
  const [streamed, setStreamed] = useState<{ taskId: string; lines: TaskLog[] }>({ taskId, lines: [] })
  const [status, setStatus] = useState<StreamStatus | "idle">("idle")
  const lastId = useRef(0)

  useEffect(() => {
    if (!live || !projectId) return
    const controller = new AbortController()
    void runEventStream({
      path: () => `/tasks/${taskId}/logs/stream?after_id=${lastId.current}`,
      projectId,
      signal: controller.signal,
      onStatus: setStatus,
      onMessage: (message) => {
        if (message.type !== "task.log" || !Array.isArray(message.lines)) return
        const lines = message.lines as TaskLog[]
        lastId.current = Math.max(lastId.current, ...lines.map((l) => l.id))
        setStreamed((prev) => ({ taskId, lines: mergeLogLines(prev.taskId === taskId ? prev.lines : [], lines) }))
      },
    })
    return () => controller.abort()
  }, [taskId, live, projectId])

  return { lines: streamed.taskId === taskId ? streamed.lines : [], status: live ? status : "idle" }
}

export function TaskLogs({ taskId, live }: { taskId: string; live: boolean }) {
  // Finished tasks read the stored log; running ones stream it (the stream starts with the backlog).
  const stored = useTaskLogs(taskId, { enabled: !live })
  const stream = useLiveLogLines(taskId, live)
  const lines = useMemo(() => mergeLogLines(stored.data?.items, stream.lines), [stored.data, stream.lines])

  const [minLevel, setMinLevel] = useState("DEBUG")
  const [query, setQuery] = useState("")
  const [follow, setFollow] = useState(true)
  const scroller = useRef<HTMLDivElement>(null)

  const visible = useMemo(() => {
    const floor = LEVEL_RANK[minLevel] ?? 0
    const needle = query.trim().toLowerCase()
    return lines.filter(
      (l) =>
        (LEVEL_RANK[l.level] ?? 0) >= floor &&
        (!needle || l.message.toLowerCase().includes(needle) || formatFields(l.fields).toLowerCase().includes(needle)),
    )
  }, [lines, minLevel, query])

  useEffect(() => {
    if (follow && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
  }, [visible, follow])

  const download = () => {
    const text = lines
      .map((l) => `${l.logged_at} [attempt ${l.attempt_number}] ${l.level.padEnd(7)} ${l.message} ${formatFields(l.fields)}`.trimEnd())
      .join("\n")
    const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" }))
    const a = Object.assign(document.createElement("a"), { href: url, download: `task-${taskId}.log` })
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!live && stored.isError) return <ErrorState error={stored.error} onRetry={() => void stored.refetch()} />
  if (!live && stored.isPending) return <RowsSkeleton rows={5} />

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            aria-label="Filter log lines"
            placeholder="Filter lines…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <Select value={minLevel} onValueChange={setMinLevel}>
          <SelectTrigger className="h-8 w-36" aria-label="Minimum level">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["DEBUG", "INFO", "WARNING", "ERROR"].map((level) => (
              <SelectItem key={level} value={level}>
                {level === "DEBUG" ? "All levels" : `${level} and up`}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Label className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
          <Switch checked={follow} onCheckedChange={setFollow} aria-label="Follow new lines" />
          <ArrowDownToLine className="size-3.5" aria-hidden /> Follow
        </Label>
        <Button variant="outline" size="sm" onClick={download} disabled={!lines.length}>
          <Download /> Download
        </Button>
        {live && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            <Radio className={cn("size-3.5", stream.status === "live" ? "text-success" : "text-warning")} aria-hidden />
            {stream.status === "live" ? "Streaming live" : stream.status === "offline" ? "Stream offline — retrying" : "Connecting…"}
          </span>
        )}
      </div>

      {!lines.length ? (
        <EmptyState
          icon={<ScrollText className="size-5" />}
          title={live ? "Waiting for log lines…" : "No log lines"}
          description={live ? "Lines appear here as the handler writes them with ctx.log." : "This task's handler did not write any log lines."}
        />
      ) : (
        <div
          ref={scroller}
          className="glass-strong max-h-[32rem] overflow-auto rounded-lg border border-border font-mono text-xs leading-relaxed"
          role="log"
          aria-live={live && follow ? "polite" : "off"}
          aria-label="Task log"
        >
          {visible.length === 0 && <p className="px-3 py-6 text-center text-muted-foreground">No lines match the filter.</p>}
          {visible.map((line, i) => {
            const header = i === 0 || visible[i - 1].attempt_number !== line.attempt_number
            return (
              <div key={line.id}>
                {header && (
                  <div className="sticky top-0 border-y border-border bg-muted/80 px-3 py-1 text-[11px] font-medium text-muted-foreground backdrop-blur">
                    Attempt {line.attempt_number}
                  </div>
                )}
                <div className="grid grid-cols-[auto_auto_1fr] gap-x-3 px-3 py-0.5 hover:bg-muted/40">
                  <time dateTime={line.logged_at} className="text-muted-foreground tabular-nums" title={line.logged_at}>
                    {timeOfDay(line.logged_at)}
                  </time>
                  <span className={cn("w-16", LEVEL_CLASS[line.level] ?? "text-foreground")}>{line.level}</span>
                  <span className="break-words whitespace-pre-wrap">
                    {line.message}
                    {line.fields && <span className="ml-2 text-muted-foreground">{formatFields(line.fields)}</span>}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {visible.length === lines.length ? `${lines.length} lines` : `${visible.length} of ${lines.length} lines`}
        {lines.length >= 2000 && " · showing the first 2,000 lines"}
      </p>
    </div>
  )
}
