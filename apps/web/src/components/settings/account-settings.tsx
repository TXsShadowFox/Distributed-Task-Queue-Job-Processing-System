"use client"

import { Loader2, Monitor, Moon, Sun } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { GlassCard } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { errorMessage } from "@/lib/api/client"
import { useChangePassword, useUpdateMe } from "@/lib/api/hooks"
import { useSession } from "@/lib/session"
import { setThemePreference, useThemePreference, type ThemePreference } from "@/lib/theme"
import { cn } from "@/lib/utils"

export function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <GlassCard className="grid gap-6 p-5 md:grid-cols-[16rem_minmax(0,1fr)] md:p-6">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </GlassCard>
  )
}

function ProfileForm() {
  const user = useSession((s) => s.user)
  const memberships = useSession((s) => s.memberships)
  const update = useUpdateMe()
  const [name, setName] = useState(user?.full_name ?? "")
  const projects = memberships.flatMap((m) => m.projects.map((p) => ({ id: p.id, label: `${m.organization_name} / ${p.name}` })))
  const defaultProject = (user?.preferences?.default_project_id as string | undefined) ?? ""
  return (
    <div className="space-y-5">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          update.mutate({ full_name: name.trim() }, { onSuccess: () => toast.success("Profile saved"), onError: (err) => toast.error(errorMessage(err)) })
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="profile-name">Full name</Label>
            <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoComplete="name" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="profile-email">Email</Label>
            <Input id="profile-email" value={user?.email ?? ""} readOnly disabled />
          </div>
        </div>
        <Button type="submit" size="sm" disabled={update.isPending || !name.trim() || name.trim() === user?.full_name}>
          {update.isPending && <Loader2 className="animate-spin" />} Save profile
        </Button>
      </form>
      {projects.length > 1 && (
        <div className="space-y-1.5">
          <Label htmlFor="default-project">Project to open after signing in</Label>
          <Select
            value={defaultProject || "__none__"}
            onValueChange={(v) =>
              update.mutate(
                { preferences: { default_project_id: v === "__none__" ? null : v } },
                { onSuccess: () => toast.success("Default project saved"), onError: (err) => toast.error(errorMessage(err)) },
              )
            }
          >
            <SelectTrigger id="default-project" className="w-full sm:w-96">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">The last one I used</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}

function PasswordForm() {
  const change = useChangePassword()
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const mismatch = confirm.length > 0 && confirm !== next
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        change.mutate(
          { current_password: current, new_password: next },
          {
            onSuccess: () => {
              toast.success("Password changed", { description: "Every session was signed out. Sign in with the new password." })
              useSession.getState().setAnonymous()
            },
            onError: (err) => toast.error(errorMessage(err)),
          },
        )
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="pw-current">Current password</Label>
          <Input id="pw-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pw-new">New password</Label>
          <Input id="pw-new" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} maxLength={128} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pw-confirm">Repeat new password</Label>
          <Input id="pw-confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-invalid={mismatch} required />
        </div>
      </div>
      <p className={cn("text-xs", mismatch ? "text-destructive" : "text-muted-foreground")}>
        {mismatch ? "The passwords do not match." : "At least 10 characters. Changing it signs out every session, including this one."}
      </p>
      <Button type="submit" size="sm" variant="outline" disabled={change.isPending || mismatch || next.length < 10 || !current}>
        {change.isPending && <Loader2 className="animate-spin" />} Change password
      </Button>
    </form>
  )
}

const THEMES: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "dark", label: "Dark", icon: Moon },
  { value: "light", label: "Light", icon: Sun },
  { value: "system", label: "System", icon: Monitor },
]

function AppearanceForm() {
  const preference = useThemePreference()
  const update = useUpdateMe()
  return (
    <div className="grid max-w-md grid-cols-3 gap-2" role="radiogroup" aria-label="Theme">
      {THEMES.map((t) => (
        <button
          key={t.value}
          type="button"
          role="radio"
          aria-checked={preference === t.value}
          onClick={() => {
            setThemePreference(t.value)
            // Remembered on the account too, so it follows you to other browsers.
            update.mutate({ preferences: { theme: t.value } })
          }}
          className={cn(
            "flex flex-col items-center gap-2 rounded-xl border border-border p-4 text-sm transition-colors hover:bg-muted/50",
            preference === t.value && "border-primary/60 bg-primary/10",
          )}
        >
          <t.icon className="size-5" aria-hidden />
          {t.label}
        </button>
      ))}
    </div>
  )
}

const NOTIFICATION_KINDS = [
  { key: "dead_letter", label: "Tasks dead-lettered", hint: "A task failed for good." },
  { key: "workflow_failed", label: "Workflow runs failed", hint: "A step failed and the run could not finish." },
  { key: "worker_offline", label: "Workers went offline", hint: "A worker stopped heartbeating without shutting down cleanly." },
] as const

function NotificationsForm() {
  const user = useSession((s) => s.user)
  const update = useUpdateMe()
  const current = (user?.preferences?.notifications ?? {}) as Record<string, boolean | undefined>
  const value = (key: string) => current[key] ?? true
  return (
    <ul className="space-y-3">
      {NOTIFICATION_KINDS.map((kind) => (
        <li key={kind.key} className="flex items-center justify-between gap-4">
          <div>
            <Label htmlFor={`notify-${kind.key}`} className="text-sm">
              {kind.label}
            </Label>
            <p className="text-xs text-muted-foreground">{kind.hint}</p>
          </div>
          <Switch
            id={`notify-${kind.key}`}
            checked={value(kind.key)}
            disabled={update.isPending}
            onCheckedChange={(on) =>
              update.mutate(
                {
                  // The server replaces the whole notifications object, so send every flag.
                  preferences: {
                    notifications: {
                      dead_letter: value("dead_letter"),
                      workflow_failed: value("workflow_failed"),
                      worker_offline: value("worker_offline"),
                      [kind.key]: on,
                    },
                  },
                },
                { onError: (err) => toast.error(errorMessage(err)) },
              )
            }
          />
        </li>
      ))}
    </ul>
  )
}

export function AccountSettings() {
  return (
    <div className="space-y-4">
      <Section title="Profile" description="How you appear in member lists and the audit log.">
        <ProfileForm />
      </Section>
      <Section title="Password" description="Used to sign in to the dashboard. API keys are managed separately.">
        <PasswordForm />
      </Section>
      <Section title="Appearance" description="Dark is the default; System follows your operating system.">
        <AppearanceForm />
      </Section>
      <Section title="Notifications" description="What appears in the bell menu for each project. Nothing is emailed.">
        <NotificationsForm />
      </Section>
    </div>
  )
}
