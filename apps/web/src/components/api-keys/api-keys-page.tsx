"use client"

import { AlertTriangle, KeyRound, Loader2, Plus, RefreshCw, ShieldOff, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import { CopyButton, EmptyState, ErrorState, GlassCard, PageHeader, RelativeTime, RowsSkeleton } from "@/components/common/primitives"
import { StatusBadge } from "@/components/common/status-badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { API_BASE, errorMessage } from "@/lib/api/client"
import { useApiKeys, useCreateApiKey, useRevokeApiKey, useRoleMatrix, useRotateApiKey } from "@/lib/api/hooks"
import type { ApiKey, ApiKeySecret } from "@/lib/api/types"
import { dateTime } from "@/lib/format"
import { useCan, useCurrentProject } from "@/lib/session"

const SCOPE_PRESETS: { label: string; scopes: string[] }[] = [
  { label: "Submit & track tasks", scopes: ["tasks:read", "tasks:write"] },
  { label: "Read-only", scopes: ["tasks:read", "workflows:read", "schedules:read", "workers:read", "analytics:read"] },
]

const EXPIRY_OPTIONS = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "Never" },
]

const GRACE_OPTIONS = [
  { value: "0", label: "Immediately" },
  { value: "3600", label: "After 1 hour" },
  { value: "86400", label: "After 24 hours" },
  { value: "604800", label: "After 7 days" },
]

/** "tasks:write" → ["tasks", "write"]; scopes are grouped by resource in the picker. */
function groupScopes(scopes: string[]): [string, string[]][] {
  const groups = new Map<string, string[]>()
  for (const scope of scopes) {
    const [resource] = scope.split(":")
    groups.set(resource, [...(groups.get(resource) ?? []), scope])
  }
  return [...groups.entries()]
}

function SecretReveal({ secret, onClose }: { secret: ApiKeySecret | null; onClose: () => void }) {
  const [stored, setStored] = useState(false)
  const origin = API_BASE.replace(/\/api\/v1$/, "")
  return (
    <Dialog
      open={!!secret}
      onOpenChange={(open) => {
        if (!open && stored) {
          setStored(false)
          onClose()
        }
      }}
    >
      <DialogContent className="sm:max-w-xl" onEscapeKeyDown={(e) => !stored && e.preventDefault()} onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Copy your new key now</DialogTitle>
          <DialogDescription>
            This is the only time the secret is shown. FlowForge stores only a hash of it, so it cannot be recovered;
            if you lose it, rotate the key.
          </DialogDescription>
        </DialogHeader>
        {secret && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 p-3">
              <code className="min-w-0 flex-1 font-mono text-sm break-all" data-testid="api-key-secret">
                {secret.secret}
              </code>
              <CopyButton value={secret.secret} label="Copy key" />
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Use it with the Python SDK</p>
              <pre className="glass-strong overflow-x-auto rounded-lg p-3 font-mono text-xs leading-relaxed">{`import os

from flowforge_sdk import FlowForgeClient

with FlowForgeClient("${origin}", api_key=os.environ["FLOWFORGE_API_KEY"]) as ff:
    task = ff.submit("send_email", {"to": "ada@example.com"})
    print(ff.wait(task.id).state)`}</pre>
              <p className="text-xs text-muted-foreground">
                Or send it as <code className="font-mono">Authorization: Bearer …</code>. The key works only in{" "}
                <span className="font-medium">{secret.key.name}</span>&apos;s project, with the scopes you chose.
              </p>
            </div>
            <Label className="flex items-center gap-2 text-sm font-normal">
              <Checkbox checked={stored} onCheckedChange={(v) => setStored(v === true)} /> I have stored the key somewhere safe
            </Label>
          </div>
        )}
        <DialogFooter>
          <Button
            disabled={!stored}
            onClick={() => {
              setStored(false)
              onClose()
            }}
          >
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CreateKeyForm({ available, onCreated }: { available: string[]; onCreated: (secret: ApiKeySecret) => void }) {
  const create = useCreateApiKey()
  const [name, setName] = useState("")
  const [scopes, setScopes] = useState<Set<string>>(() => new Set(["tasks:read", "tasks:write"]))
  const [expiry, setExpiry] = useState("90")
  const toggle = (scope: string, on: boolean) => {
    const next = new Set(scopes)
    if (on) next.add(scope)
    else next.delete(scope)
    setScopes(next)
  }
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        create.mutate(
          { name: name.trim(), scopes: [...scopes], expires_in_days: expiry === "never" ? null : Number(expiry) },
          { onSuccess: onCreated, onError: (error) => toast.error(errorMessage(error)) },
        )
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="key-name">Name</Label>
        <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="billing-service (production)" required maxLength={120} />
        <p className="text-xs text-muted-foreground">Say what uses it, so you know what breaks when you revoke it.</p>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Scopes</legend>
        <div className="flex flex-wrap gap-1.5">
          {SCOPE_PRESETS.map((preset) => (
            <Button key={preset.label} type="button" variant="outline" size="xs" onClick={() => setScopes(new Set(preset.scopes.filter((s) => available.includes(s))))}>
              {preset.label}
            </Button>
          ))}
          <Button type="button" variant="outline" size="xs" onClick={() => setScopes(new Set(available))}>
            Everything a key may have
          </Button>
        </div>
        <div className="grid gap-x-6 gap-y-2 rounded-lg border border-border p-3 sm:grid-cols-2">
          {groupScopes(available).map(([resource, list]) => (
            <div key={resource} className="space-y-1">
              <p className="text-xs font-medium capitalize text-muted-foreground">{resource}</p>
              {list.map((scope) => (
                <Label key={scope} className="flex items-center gap-2 font-mono text-xs font-normal">
                  <Checkbox checked={scopes.has(scope)} onCheckedChange={(v) => toggle(scope, v === true)} />
                  {scope}
                </Label>
              ))}
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Keys can never manage members, other keys or the organization; those actions need a signed-in person.
        </p>
      </fieldset>
      <div className="space-y-1.5">
        <Label htmlFor="key-expiry">Expires</Label>
        <Select value={expiry} onValueChange={setExpiry}>
          <SelectTrigger id="key-expiry" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EXPIRY_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <DialogFooter>
        <Button type="submit" disabled={create.isPending || !name.trim() || scopes.size === 0}>
          {create.isPending && <Loader2 className="animate-spin" />} Create key
        </Button>
      </DialogFooter>
    </form>
  )
}

function RotateDialog({ apiKey, onClose, onRotated }: { apiKey: ApiKey | null; onClose: () => void; onRotated: (s: ApiKeySecret) => void }) {
  const rotate = useRotateApiKey()
  const [grace, setGrace] = useState("86400")
  return (
    <Dialog open={!!apiKey} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rotate {apiKey?.name}</DialogTitle>
          <DialogDescription>
            A new secret with the same scopes is issued. Keep the old one working for a grace period so you can roll
            the new secret out without downtime.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="rotate-grace">Old secret stops working</Label>
          <Select value={grace} onValueChange={setGrace}>
            <SelectTrigger id="rotate-grace" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GRACE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <DialogFooter>
          <Button
            disabled={rotate.isPending || !apiKey}
            onClick={() =>
              apiKey &&
              rotate.mutate(
                { id: apiKey.id, grace: Number(grace) },
                {
                  onSuccess: (secret) => {
                    onClose()
                    onRotated(secret)
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                },
              )
            }
          >
            {rotate.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Rotate key
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ApiKeysPage() {
  const can = useCan()
  const project = useCurrentProject()
  const keys = useApiKeys(can.admin)
  const matrix = useRoleMatrix()
  const revoke = useRevokeApiKey()
  const [creating, setCreating] = useState(false)
  const [rotating, setRotating] = useState<ApiKey | null>(null)
  const [secret, setSecret] = useState<ApiKeySecret | null>(null)
  const [showRevoked, setShowRevoked] = useState(false)

  const rows = useMemo(() => (keys.data ?? []).filter((k) => showRevoked || k.status !== "revoked"), [keys.data, showRevoked])
  const revokedCount = (keys.data ?? []).filter((k) => k.status === "revoked").length

  if (!can.admin) {
    return (
      <div className="space-y-6">
        <PageHeader title="API keys" description="Project-scoped credentials for services and the SDK." />
        <GlassCard>
          <EmptyState
            icon={<ShieldOff className="size-5" />}
            title="Admins manage API keys"
            description={`Your role in ${project?.name ?? "this project"} is ${can.role}. Ask an admin or owner for a key.`}
          />
        </GlassCard>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="API keys"
        description={`Credentials for services and the Python SDK. Each key works only in ${project?.name ?? "this project"}, with the scopes it was given.`}
        actions={
          <>
            {revokedCount > 0 && (
              <Label className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
                <Switch checked={showRevoked} onCheckedChange={setShowRevoked} /> Show revoked ({revokedCount})
              </Label>
            )}
            <Button onClick={() => setCreating(true)}>
              <Plus /> Create key
            </Button>
          </>
        }
      />

      <GlassCard className="overflow-hidden">
        {keys.isError ? (
          <ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
        ) : keys.isPending ? (
          <RowsSkeleton rows={3} />
        ) : !rows.length ? (
          <EmptyState
            icon={<KeyRound className="size-5" />}
            title="No API keys"
            description="Create one for each service that submits tasks, so you can rotate or revoke them independently."
            action={
              <Button onClick={() => setCreating(true)}>
                <Plus /> Create your first key
              </Button>
            }
          />
        ) : (
          <div className="glass-strong overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Scopes</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead className="pr-4 text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((k) => (
                  <TableRow key={k.id} data-key-name={k.name}>
                    <TableCell className="pl-4">
                      <p className="font-medium">{k.name}</p>
                      <p className="text-xs text-muted-foreground">
                        Created <RelativeTime value={k.created_at} />
                        {k.rotated_from_id && " · rotated"}
                      </p>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{k.prefix}…</TableCell>
                    <TableCell>
                      <div className="flex max-w-72 flex-wrap gap-1">
                        {k.scopes.map((s) => (
                          <span key={s} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                            {s}
                          </span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={k.status.toUpperCase()} animate={false} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {k.last_used_at ? <RelativeTime value={k.last_used_at} /> : "Never"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {k.revoked_at ? (
                        <span title={dateTime(k.revoked_at)}>Revoked</span>
                      ) : k.expires_at ? (
                        <span title={dateTime(k.expires_at)}>
                          <RelativeTime value={k.expires_at} />
                        </span>
                      ) : (
                        "Never"
                      )}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      {k.status !== "revoked" && k.status !== "expired" && (
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" size="sm" onClick={() => setRotating(k)}>
                            <RefreshCw /> Rotate
                          </Button>
                          <Confirm
                            trigger={
                              <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive">
                                <Trash2 /> Revoke
                              </Button>
                            }
                            title={`Revoke ${k.name}?`}
                            description="Requests using this key are rejected immediately. This cannot be undone; create a new key if you need access again."
                            confirmLabel="Revoke key"
                            dismissLabel="Keep it"
                            destructive
                            onConfirm={() =>
                              revoke.mutate(k.id, {
                                onSuccess: () => toast.success(`Revoked ${k.name}`),
                                onError: (e) => toast.error(errorMessage(e)),
                              })
                            }
                          />
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </GlassCard>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        Treat keys like passwords: keep them in a secret manager or environment variable, never in source control.
        Keys are stored as SHA-256 hashes; only the prefix above is kept in clear text for identification.
      </p>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Create an API key</DialogTitle>
            <DialogDescription>Give it the fewest scopes the service needs.</DialogDescription>
          </DialogHeader>
          {creating &&
            (matrix.data ? (
              <CreateKeyForm
                available={matrix.data.api_key_scopes}
                onCreated={(s) => {
                  setCreating(false)
                  setSecret(s)
                }}
              />
            ) : matrix.isError ? (
              <ErrorState error={matrix.error} onRetry={() => void matrix.refetch()} />
            ) : (
              <Loader2 className="mx-auto my-8 animate-spin text-muted-foreground" />
            ))}
        </DialogContent>
      </Dialog>
      <RotateDialog apiKey={rotating} onClose={() => setRotating(null)} onRotated={setSecret} />
      <SecretReveal secret={secret} onClose={() => setSecret(null)} />
    </div>
  )
}
