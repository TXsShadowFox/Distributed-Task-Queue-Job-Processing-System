"use client"

import { Loader2, Send, Wand2 } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useSubmitTask, useTaskTypes } from "@/lib/api/hooks"
import type { TaskSubmit, TaskType } from "@/lib/api/types"
import { parseJsonObject, prettyJson } from "@/lib/json"
import { useCan } from "@/lib/session"
import { useUi } from "@/lib/ui-store"

function numberOrNull(value: string): number | null {
  if (value.trim() === "") return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** Mounted fresh every time the dialog opens, so all state comes from lazy initializers (no effect-driven resets). */
function SubmitTaskForm({ types, presetType, onDone }: { types: TaskType[]; presetType: string | null; onDone: () => void }) {
  const submit = useSubmitTask()
  const router = useRouter()
  const initial = types.find((t) => t.name === presetType) ?? types.find((t) => t.name === "generate_report") ?? types[0]

  const [taskType, setTaskType] = useState(initial.name)
  const [payload, setPayload] = useState(() => prettyJson(initial.example_payload))
  const [priority, setPriority] = useState("")
  const [maxRetries, setMaxRetries] = useState("")
  const [timeout, setTimeoutValue] = useState("")
  const [delay, setDelay] = useState("")
  const [idempotencyKey, setIdempotencyKey] = useState("")
  const [deadLetter, setDeadLetter] = useState(true)
  const [fieldErrors, setFieldErrors] = useState<string[]>([])

  const selected = useMemo(() => types.find((t) => t.name === taskType), [types, taskType])
  const parsed = parseJsonObject(payload, "Payload")

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!selected || parsed.error || !parsed.value) return
    setFieldErrors([])
    const body: TaskSubmit = {
      task_type: selected.name,
      payload: parsed.value,
      priority: numberOrNull(priority),
      max_retries: numberOrNull(maxRetries),
      timeout_seconds: numberOrNull(timeout),
      delay_seconds: numberOrNull(delay),
      dead_letter: deadLetter,
      idempotency_key: idempotencyKey.trim() || null,
    }
    try {
      const task = await submit.mutateAsync({ body })
      toast.success(`${task.task_type} ${task.state === "SCHEDULED" ? "scheduled" : "queued"}`, {
        action: { label: "Open", onClick: () => router.push(`/tasks/${task.id}`) },
      })
      onDone()
    } catch (error) {
      if (error instanceof ApiError && Array.isArray(error.details)) {
        setFieldErrors(
          (error.details as { loc?: string[]; msg?: string }[]).map((d) => `${(d.loc ?? []).join(".")}: ${d.msg ?? ""}`),
        )
      }
      toast.error(errorMessage(error))
    }
  }

  return (
    <form onSubmit={onSubmit}>
      <div className="my-5 grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="task-type">Task type</Label>
          <Select
            value={taskType}
            onValueChange={(v) => {
              setTaskType(v)
              setPayload(prettyJson(types.find((x) => x.name === v)?.example_payload))
            }}
          >
            <SelectTrigger id="task-type" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {types.map((t) => (
                <SelectItem key={t.name} value={t.name}>
                  <span className="font-mono text-xs">{t.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">queue: {t.queue}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selected && <p className="text-xs text-muted-foreground">{selected.description}</p>}
        </div>

        <div className="space-y-1.5 sm:col-span-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="payload">Payload (JSON)</Label>
            {selected && (
              <Button type="button" variant="ghost" size="xs" onClick={() => setPayload(prettyJson(selected.example_payload))}>
                <Wand2 /> Use example
              </Button>
            )}
          </div>
          <Textarea
            id="payload"
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            rows={7}
            spellCheck={false}
            className="font-mono text-xs"
            aria-invalid={!!parsed.error}
            aria-describedby="payload-error"
          />
          {parsed.error && (
            <p id="payload-error" className="text-xs text-destructive" role="alert">
              {parsed.error}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="priority">Priority (0–9)</Label>
          <Input id="priority" inputMode="numeric" placeholder={`default ${selected?.default_priority ?? 5}`} value={priority} onChange={(e) => setPriority(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="retries">Max retries</Label>
          <Input id="retries" inputMode="numeric" placeholder={`default ${selected?.default_max_retries ?? 3}`} value={maxRetries} onChange={(e) => setMaxRetries(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="timeout">Timeout (seconds)</Label>
          <Input id="timeout" inputMode="numeric" placeholder={`default ${selected?.default_timeout_seconds ?? 300}`} value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="delay">Delay (seconds)</Label>
          <Input id="delay" inputMode="decimal" placeholder="run immediately" value={delay} onChange={(e) => setDelay(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="idem">Idempotency key</Label>
          <Input id="idem" placeholder="optional, e.g. order-42" value={idempotencyKey} onChange={(e) => setIdempotencyKey(e.target.value)} />
        </div>
        <div className="flex items-center justify-between gap-3 rounded-xl border border-border px-3 py-2">
          <Label htmlFor="dlq" className="text-sm font-normal">
            Dead-letter on permanent failure
          </Label>
          <Switch id="dlq" checked={deadLetter} onCheckedChange={setDeadLetter} />
        </div>
        {fieldErrors.length > 0 && (
          <ul className="space-y-1 rounded-lg bg-destructive/10 p-3 text-xs text-destructive sm:col-span-2" role="alert">
            {fieldErrors.map((e) => (
              <li key={e} className="font-mono">
                {e}
              </li>
            ))}
          </ul>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={!selected || !!parsed.error || submit.isPending}>
          {submit.isPending ? <Loader2 className="animate-spin" /> : <Send />} Submit
        </Button>
      </DialogFooter>
    </form>
  )
}

export function SubmitTaskDialog() {
  const open = useUi((s) => s.submitTaskOpen)
  const presetType = useUi((s) => s.submitTaskType)
  const close = useUi((s) => s.closeSubmitTask)
  const { data: types, isLoading, isError } = useTaskTypes()
  const can = useCan()

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Submit a task</DialogTitle>
          <DialogDescription>
            Payloads are validated against the JSON Schema each task type&apos;s workers publish.
          </DialogDescription>
        </DialogHeader>
        {!can.write ? (
          <p className="my-6 rounded-lg bg-muted p-4 text-sm text-muted-foreground">
            Your role ({can.role}) is read-only in this project. Ask an admin for developer access to submit tasks.
          </p>
        ) : isLoading ? (
          <p className="my-6 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading task types…
          </p>
        ) : isError || !types?.length ? (
          <p className="my-6 rounded-lg bg-muted p-4 text-sm text-muted-foreground">
            No task types are registered yet. Task types are published by workers when they start — run{" "}
            <code className="font-mono text-xs">flowforge-worker</code> and try again.
          </p>
        ) : (
          <SubmitTaskForm types={types} presetType={presetType} onDone={close} />
        )}
      </DialogContent>
    </Dialog>
  )
}
