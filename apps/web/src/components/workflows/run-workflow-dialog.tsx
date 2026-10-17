"use client"

import { Loader2, Play } from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/api/client"
import { useExecuteWorkflow } from "@/lib/api/hooks"
import { parseJsonObject } from "@/lib/json"

function RunForm({
  workflowId,
  versions,
  latestVersion,
  onDone,
}: {
  workflowId: string
  versions: number[]
  latestVersion: number
  onDone: () => void
}) {
  const execute = useExecuteWorkflow(workflowId)
  const router = useRouter()
  const [input, setInput] = useState("{\n  \n}")
  const [version, setVersion] = useState(String(latestVersion))
  const parsed = parseJsonObject(input, "Run input")

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!parsed.value) return
    execute.mutate(
      { input: parsed.value, version: Number(version) },
      {
        onSuccess: (run) => {
          toast.success("Workflow run started")
          onDone()
          router.push(`/workflows/runs/${run.id}`)
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {versions.length > 1 && (
        <div className="space-y-1.5">
          <Label htmlFor="run-version">Version</Label>
          <Select value={version} onValueChange={setVersion}>
            <SelectTrigger id="run-version" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[...versions].reverse().map((v) => (
                <SelectItem key={v} value={String(v)}>
                  v{v}
                  {v === latestVersion ? " (latest)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="run-input">Run input (JSON)</Label>
        <Textarea
          id="run-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={7}
          spellCheck={false}
          className="font-mono text-xs"
          aria-invalid={!!parsed.error}
          aria-describedby="run-input-help"
        />
        <p id="run-input-help" className={parsed.error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
          {parsed.error ?? "Every step's handler can read this as ctx.workflow_input, and its upstream steps' results as ctx.upstream."}
        </p>
      </div>
      <DialogFooter>
        <Button type="submit" disabled={!!parsed.error || execute.isPending}>
          {execute.isPending ? <Loader2 className="animate-spin" /> : <Play />} Start run
        </Button>
      </DialogFooter>
    </form>
  )
}

export function RunWorkflowDialog({
  open,
  onOpenChange,
  workflow,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workflow: { id: string; name: string; latest_version: number; versions?: number[] } | null
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Run {workflow?.name ?? "workflow"}</DialogTitle>
          <DialogDescription>
            Nodes start as soon as all of their upstream nodes succeed. If a node fails for good, everything downstream
            of it is skipped while independent branches keep running; retrying that node&apos;s task resumes the run.
          </DialogDescription>
        </DialogHeader>
        {workflow && open && (
          <RunForm
            workflowId={workflow.id}
            versions={workflow.versions ?? [workflow.latest_version]}
            latestVersion={workflow.latest_version}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
