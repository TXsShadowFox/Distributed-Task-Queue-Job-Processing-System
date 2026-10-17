"use client"

import { Loader2, Wand2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { CronField } from "@/components/schedules/cron-field"
import { browserTimeZone, TimezonePicker } from "@/components/schedules/timezone-picker"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ApiError, errorMessage } from "@/lib/api/client"
import { useCreateSchedule, useTaskTypes, useUpdateSchedule } from "@/lib/api/hooks"
import type { Schedule, ScheduleIn, TaskType } from "@/lib/api/types"
import { parseJsonObject, prettyJson } from "@/lib/json"

const numberOrNull = (value: string) => (value.trim() === "" ? null : Number(value))

function ScheduleForm({ types, existing, onDone }: { types: TaskType[]; existing: Schedule | null; onDone: () => void }) {
  const create = useCreateSchedule()
  const update = useUpdateSchedule()
  const firstType = types.find((t) => t.name === "generate_report") ?? types[0]

  const [name, setName] = useState(existing?.name ?? "")
  const [description, setDescription] = useState(existing?.description ?? "")
  const [expression, setExpression] = useState(existing?.cron_expression ?? "0 9 * * 1-5")
  const [timezone, setTimezone] = useState(existing?.timezone ?? browserTimeZone())
  const [taskType, setTaskType] = useState(existing?.task_type ?? firstType?.name ?? "")
  const [payload, setPayload] = useState(() => prettyJson(existing ? existing.payload : firstType?.example_payload))
  const [priority, setPriority] = useState(existing ? String(existing.priority) : "")
  const [maxRetries, setMaxRetries] = useState(existing ? String(existing.max_retries) : "")
  const [timeout, setTimeoutValue] = useState(existing ? String(existing.timeout_seconds) : "")
  const [misfire, setMisfire] = useState<"RUN_ONCE" | "SKIP">((existing?.misfire_policy as "RUN_ONCE" | "SKIP") ?? "RUN_ONCE")
  const [overlap, setOverlap] = useState<"ALLOW" | "SKIP">((existing?.overlap_policy as "ALLOW" | "SKIP") ?? "ALLOW")
  const [paused, setPaused] = useState(existing?.status === "PAUSED")

  const type = types.find((t) => t.name === taskType)
  const parsed = parseJsonObject(payload, "Payload")
  const pending = create.isPending || update.isPending

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!parsed.value) return
    const body: ScheduleIn = {
      name: name.trim(),
      description: description.trim() || null,
      cron_expression: expression.trim(),
      timezone,
      task_type: taskType,
      payload: parsed.value,
      priority: numberOrNull(priority),
      max_retries: numberOrNull(maxRetries),
      timeout_seconds: numberOrNull(timeout),
      misfire_policy: misfire,
      overlap_policy: overlap,
      paused,
    }
    const onError = (error: unknown) => {
      const details = error instanceof ApiError && Array.isArray(error.details) ? (error.details as { loc?: string[]; msg?: string }[]) : []
      toast.error(errorMessage(error), {
        description: details.map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`).join("; ") || undefined,
      })
    }
    if (existing) {
      // No expected_version: the scheduler bumps the row on every fire, and a patch only touches the fields sent.
      update.mutate(
        { id: existing.id, patch: { ...body, description: description.trim() } },
        { onSuccess: () => (toast.success(`Updated “${body.name}”`), onDone()), onError },
      )
    } else {
      create.mutate(body, { onSuccess: (s) => (toast.success(`Created “${s.name}”`), onDone()), onError })
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="schedule-name">Name</Label>
          <Input id="schedule-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} placeholder="Nightly report" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="schedule-tz">Time zone</Label>
          <TimezonePicker id="schedule-tz" value={timezone} onChange={setTimezone} />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="schedule-description">Description</Label>
          <Input id="schedule-description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} placeholder="Optional" />
        </div>
      </div>

      <CronField expression={expression} timezone={timezone} onChange={setExpression} />

      <div className="space-y-1.5">
        <Label htmlFor="schedule-type">Task type</Label>
        <Select
          value={taskType}
          onValueChange={(v) => {
            setTaskType(v)
            setPayload(prettyJson(types.find((t) => t.name === v)?.example_payload))
          }}
        >
          <SelectTrigger id="schedule-type" className="w-full">
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
        {type && <p className="text-xs text-muted-foreground">{type.description}</p>}
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="schedule-payload">Payload (JSON)</Label>
          {type && (
            <Button type="button" variant="ghost" size="xs" onClick={() => setPayload(prettyJson(type.example_payload))}>
              <Wand2 /> Use example
            </Button>
          )}
        </div>
        <Textarea
          id="schedule-payload"
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
          rows={5}
          spellCheck={false}
          className="font-mono text-xs"
          aria-invalid={!!parsed.error}
        />
        {parsed.error && (
          <p className="text-xs text-destructive" role="alert">
            {parsed.error}
          </p>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="schedule-priority">Priority (0–9)</Label>
          <Input id="schedule-priority" inputMode="numeric" value={priority} onChange={(e) => setPriority(e.target.value)} placeholder={String(type?.default_priority ?? 5)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="schedule-retries">Max retries</Label>
          <Input id="schedule-retries" inputMode="numeric" value={maxRetries} onChange={(e) => setMaxRetries(e.target.value)} placeholder={String(type?.default_max_retries ?? 3)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="schedule-timeout">Timeout (s)</Label>
          <Input id="schedule-timeout" inputMode="numeric" value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} placeholder={String(type?.default_timeout_seconds ?? 300)} />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="schedule-misfire">If runs were missed</Label>
          <Select value={misfire} onValueChange={(v) => setMisfire(v as "RUN_ONCE" | "SKIP")}>
            <SelectTrigger id="schedule-misfire" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="RUN_ONCE">Run once to catch up</SelectItem>
              <SelectItem value="SKIP">Skip them</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">For example after the scheduler was down.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="schedule-overlap">If the previous run is still going</Label>
          <Select value={overlap} onValueChange={(v) => setOverlap(v as "ALLOW" | "SKIP")}>
            <SelectTrigger id="schedule-overlap" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALLOW">Start another run</SelectItem>
              <SelectItem value="SKIP">Skip this run</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <Label className="flex items-center gap-2 text-sm font-normal">
        <Switch checked={paused} onCheckedChange={setPaused} /> {existing ? "Paused" : "Create paused"}
      </Label>

      <DialogFooter>
        <Button type="submit" disabled={pending || !!parsed.error || !name.trim() || !taskType}>
          {pending && <Loader2 className="animate-spin" />}
          {existing ? "Save changes" : "Create schedule"}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function ScheduleDialog({
  open,
  onOpenChange,
  existing,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existing: Schedule | null
}) {
  const { data: types, isPending } = useTaskTypes()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{existing ? `Edit ${existing.name}` : "New schedule"}</DialogTitle>
          <DialogDescription>
            Each fire submits one task. Fire times are computed in the schedule&apos;s time zone and recorded so a fire
            never runs twice, even with several scheduler replicas.
          </DialogDescription>
        </DialogHeader>
        {open && (isPending ? <Loader2 className="mx-auto my-10 animate-spin text-muted-foreground" /> : <ScheduleForm types={types ?? []} existing={existing} onDone={() => onOpenChange(false)} />)}
      </DialogContent>
    </Dialog>
  )
}
