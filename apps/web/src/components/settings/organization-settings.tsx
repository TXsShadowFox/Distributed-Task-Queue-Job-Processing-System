"use client"

import { Check, FolderPlus, Loader2, Minus, Trash2, UserPlus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Confirm } from "@/components/common/confirm"
import { ErrorState, RelativeTime, RowsSkeleton } from "@/components/common/primitives"
import { Section } from "@/components/settings/account-settings"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, errorMessage } from "@/lib/api/client"
import {
  useMemberMutations,
  useMembers,
  useOrganization,
  useProjectMutations,
  useProjects,
  useRoleMatrix,
  useUpdateOrganization,
} from "@/lib/api/hooks"
import type { Me, Member, ProjectAccess } from "@/lib/api/types"
import { useCan, useCurrentMembership, useSession } from "@/lib/session"

const ROLES = ["owner", "admin", "developer", "viewer"] as const
const RANK: Record<string, number> = { viewer: 0, developer: 1, admin: 2, owner: 3 }

/** Mirrors the server rule (flowforge_shared.rbac.can_grant_role) for display only; the API enforces it. */
function grantable(actorRole: string): string[] {
  if (actorRole === "owner") return [...ROLES]
  if (actorRole === "admin") return ROLES.filter((r) => r !== "owner")
  return []
}

function OrganizationName({ orgId }: { orgId: string }) {
  const org = useOrganization(orgId)
  const update = useUpdateOrganization(orgId)
  const can = useCan()
  const [draft, setDraft] = useState<string | null>(null)
  const name = draft ?? org.data?.name ?? ""
  if (org.isError) return <ErrorState error={org.error} onRetry={() => void org.refetch()} />
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        update.mutate(name.trim(), {
          onSuccess: async () => {
            setDraft(null)
            toast.success("Organization renamed")
            // The name also appears in the project switcher, which reads it from /auth/me.
            useSession.getState().setMe(await api<Me>("/auth/me", { project: false }))
          },
          onError: (err) => toast.error(errorMessage(err)),
        })
      }}
    >
      <div className="min-w-64 flex-1 space-y-1.5">
        <Label htmlFor="org-name">Name</Label>
        <Input id="org-name" value={name} onChange={(e) => setDraft(e.target.value)} disabled={!can.owner} maxLength={120} required />
        <p className="text-xs text-muted-foreground">
          Slug <span className="font-mono">{org.data?.slug}</span> · created {org.data && <RelativeTime value={org.data.created_at} />}
          {!can.owner && " · only owners can rename it"}
        </p>
      </div>
      {can.owner && (
        <Button type="submit" size="sm" disabled={update.isPending || !name.trim() || name.trim() === org.data?.name}>
          {update.isPending && <Loader2 className="animate-spin" />} Rename
        </Button>
      )}
    </form>
  )
}

function MemberRow({ member, orgId, selfId, actorRole }: { member: Member; orgId: string; selfId?: string; actorRole: string }) {
  const { update, remove } = useMemberMutations(orgId)
  const canManage = grantable(actorRole).length > 0 && (member.role !== "owner" || actorRole === "owner")
  const self = member.user_id === selfId
  return (
    <TableRow>
      <TableCell className="pl-4">
        <p className="font-medium">
          {member.full_name}
          {self && <span className="ml-1.5 text-xs text-muted-foreground">(you)</span>}
        </p>
        <p className="text-xs text-muted-foreground">{member.email}</p>
      </TableCell>
      <TableCell>
        {canManage && !self ? (
          <Select
            value={member.role}
            onValueChange={(role) =>
              update.mutate(
                { memberId: member.id, role },
                { onSuccess: () => toast.success(`${member.full_name} is now ${role}`), onError: (err) => toast.error(errorMessage(err)) },
              )
            }
          >
            <SelectTrigger className="h-8 w-32" aria-label={`Role of ${member.full_name}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {grantable(actorRole).map((r) => (
                <SelectItem key={r} value={r} className="capitalize">
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <span className="capitalize">{member.role}</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <RelativeTime value={member.created_at} />
      </TableCell>
      <TableCell className="pr-4 text-right">
        {canManage && !self && (
          <Confirm
            trigger={
              <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" aria-label={`Remove ${member.full_name}`}>
                <Trash2 /> Remove
              </Button>
            }
            title={`Remove ${member.full_name}?`}
            description="They lose access to every project in this organization immediately. Their tasks and audit entries are kept."
            confirmLabel="Remove member"
            dismissLabel="Keep"
            destructive
            onConfirm={() =>
              remove.mutate(member.id, {
                onSuccess: () => toast.success(`Removed ${member.full_name}`),
                onError: (err) => toast.error(errorMessage(err)),
              })
            }
          />
        )}
      </TableCell>
    </TableRow>
  )
}

function Members({ orgId, actorRole }: { orgId: string; actorRole: string }) {
  const members = useMembers(orgId)
  const { add } = useMemberMutations(orgId)
  const selfId = useSession((s) => s.user?.id)
  const [email, setEmail] = useState("")
  const [role, setRole] = useState("developer")
  const roles = grantable(actorRole)
  return (
    <div className="space-y-4">
      {roles.length > 0 && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate(
              { email: email.trim(), role },
              {
                onSuccess: (m) => {
                  setEmail("")
                  toast.success(`Added ${m.full_name} as ${m.role}`)
                },
                onError: (err) => toast.error(errorMessage(err)),
              },
            )
          }}
        >
          <div className="min-w-56 flex-1 space-y-1.5">
            <Label htmlFor="member-email">Add an existing FlowForge user</Label>
            <Input id="member-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="teammate@example.com" required />
          </div>
          <Select value={role} onValueChange={setRole}>
            <SelectTrigger className="w-36" aria-label="Role for the new member">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {roles.map((r) => (
                <SelectItem key={r} value={r} className="capitalize">
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" disabled={add.isPending || !email.trim()}>
            {add.isPending ? <Loader2 className="animate-spin" /> : <UserPlus />} Add
          </Button>
        </form>
      )}
      {members.isError ? (
        <ErrorState error={members.error} onRetry={() => void members.refetch()} />
      ) : members.isPending ? (
        <RowsSkeleton rows={3} />
      ) : (
        <div className="glass-strong overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Joined</TableHead>
                <TableHead className="pr-4 text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...members.data]
                .sort((a, b) => RANK[b.role] - RANK[a.role] || a.full_name.localeCompare(b.full_name))
                .map((m) => (
                  <MemberRow key={m.id} member={m} orgId={orgId} selfId={selfId} actorRole={actorRole} />
                ))}
            </TableBody>
          </Table>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        People must register before they can be added; there are no email invitations. Every organization keeps at least
        one owner.
      </p>
    </div>
  )
}

function RoleMatrix() {
  const matrix = useRoleMatrix()
  if (matrix.isError) return <ErrorState error={matrix.error} onRetry={() => void matrix.refetch()} />
  if (!matrix.data) return <RowsSkeleton rows={4} />
  const permissions = [...new Set(Object.values(matrix.data.roles).flat())].sort()
  const keyScopes = new Set(matrix.data.api_key_scopes)
  return (
    <div className="glass-strong overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Permission</TableHead>
            {ROLES.map((r) => (
              <TableHead key={r} className="text-center capitalize">
                {r}
              </TableHead>
            ))}
            <TableHead className="pr-4 text-center">API keys</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {permissions.map((p) => (
            <TableRow key={p}>
              <TableCell className="pl-4 font-mono text-xs">{p}</TableCell>
              {ROLES.map((r) => (
                <TableCell key={r} className="text-center">
                  {matrix.data.roles[r]?.includes(p) ? (
                    <Check className="mx-auto size-4 text-success" aria-label="allowed" />
                  ) : (
                    <Minus className="mx-auto size-4 text-muted-foreground/50" aria-label="not allowed" />
                  )}
                </TableCell>
              ))}
              <TableCell className="pr-4 text-center">
                {keyScopes.has(p) ? (
                  <Check className="mx-auto size-4 text-success" aria-label="can be granted" />
                ) : (
                  <Minus className="mx-auto size-4 text-muted-foreground/50" aria-label="cannot be granted" />
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function ProjectRow({ project, canManage }: { project: ProjectAccess; canManage: boolean }) {
  const { update } = useProjectMutations()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? "")
  const [retention, setRetention] = useState(String((project.settings?.retention_days as number | undefined) ?? ""))
  if (!editing) {
    return (
      <li className="flex flex-wrap items-center justify-between gap-3 py-3">
        <div className="min-w-0">
          <p className="font-medium">
            {project.name} <span className="ml-1 font-mono text-xs text-muted-foreground">{project.slug}</span>
          </p>
          <p className="text-xs text-muted-foreground">
            {project.description || "No description"} · your role: <span className="capitalize">{project.role}</span> ·{" "}
            {typeof project.settings?.retention_days === "number"
              ? `history kept ${project.settings.retention_days} days`
              : "history kept for the platform default"}
          </p>
        </div>
        {canManage && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
      </li>
    )
  }
  return (
    <li className="py-3">
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          update.mutate(
            {
              projectId: project.id,
              name: name.trim(),
              description: description.trim(),
              settings: { retention_days: retention.trim() ? Number(retention) : null },
            },
            {
              onSuccess: () => {
                setEditing(false)
                toast.success("Project saved")
              },
              onError: (err) => toast.error(errorMessage(err)),
            },
          )
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor={`project-name-${project.id}`}>Name</Label>
          <Input id={`project-name-${project.id}`} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`project-desc-${project.id}`}>Description</Label>
          <Input id={`project-desc-${project.id}`} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`project-retention-${project.id}`}>Keep history (days)</Label>
          <Input
            id={`project-retention-${project.id}`}
            inputMode="numeric"
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
            placeholder="platform default"
          />
        </div>
        <div className="flex gap-2">
          <Button type="submit" size="sm" disabled={update.isPending || !name.trim()}>
            {update.isPending && <Loader2 className="animate-spin" />} Save
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </form>
      <p className="mt-2 text-xs text-muted-foreground">
        Finished tasks and workflow runs older than this are deleted by the scheduler&apos;s retention job.
      </p>
    </li>
  )
}

function Projects({ orgId, canManage }: { orgId: string; canManage: boolean }) {
  const projects = useProjects()
  const { create } = useProjectMutations()
  const [name, setName] = useState("")
  const mine = (projects.data ?? []).filter((p) => p.organization_id === orgId)
  return (
    <div className="space-y-3">
      {projects.isError ? (
        <ErrorState error={projects.error} onRetry={() => void projects.refetch()} />
      ) : projects.isPending ? (
        <RowsSkeleton rows={2} />
      ) : (
        <ul className="divide-y divide-border">
          {mine.map((p) => (
            <ProjectRow key={p.id} project={p} canManage={canManage} />
          ))}
        </ul>
      )}
      {canManage && (
        <form
          className="flex flex-wrap items-end gap-2 border-t border-border pt-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate(
              { orgId, name: name.trim() },
              {
                onSuccess: (p) => {
                  setName("")
                  toast.success(`Created ${p.name}`, { description: "Switch to it from the project menu in the top bar." })
                },
                onError: (err) => toast.error(errorMessage(err)),
              },
            )
          }}
        >
          <div className="min-w-56 flex-1 space-y-1.5">
            <Label htmlFor="new-project">New project</Label>
            <Input id="new-project" value={name} onChange={(e) => setName(e.target.value)} placeholder="payments-staging" maxLength={120} required />
          </div>
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <FolderPlus />} Create project
          </Button>
        </form>
      )}
      <p className="text-xs text-muted-foreground">
        Projects isolate tasks, workflows, schedules and API keys. Members get the same role in every project of the
        organization.
      </p>
    </div>
  )
}

export function OrganizationSettings() {
  const membership = useCurrentMembership()
  const can = useCan()
  if (!membership) return null
  const orgId = membership.organization_id
  return (
    <div className="space-y-4">
      <Section title="Organization" description={`You are ${/^[aeiou]/.test(membership.role) ? "an" : "a"} ${membership.role} of ${membership.organization_name}.`}>
        <OrganizationName orgId={orgId} />
      </Section>
      <Section title="Members" description="Owners and admins manage members. Admins cannot grant or change the owner role.">
        <Members orgId={orgId} actorRole={membership.role} />
      </Section>
      <Section title="Projects" description="Admins and owners create and configure projects.">
        <Projects orgId={orgId} canManage={can.admin} />
      </Section>
      <Section title="Roles" description="What each role may do, as enforced by the API. API keys can hold only the scopes in the last column.">
        <RoleMatrix />
      </Section>
    </div>
  )
}
