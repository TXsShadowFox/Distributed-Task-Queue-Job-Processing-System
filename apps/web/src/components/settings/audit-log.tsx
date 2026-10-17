"use client"

import { ChevronDown, ChevronLeft, ChevronRight, ScrollText } from "lucide-react"
import { Fragment, useState } from "react"

import { EmptyState, ErrorState, GlassCard, JsonView, RowsSkeleton } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useAuditLogs } from "@/lib/api/hooks"
import { dateTime } from "@/lib/format"
import { useCan, useCurrentMembership } from "@/lib/session"
import { cn } from "@/lib/utils"

export function AuditLog() {
  const membership = useCurrentMembership()
  const can = useCan()
  // Each entry of the stack is the `before_id` that produced a page; undefined = the newest page.
  const [pages, setPages] = useState<(number | undefined)[]>([undefined])
  const [open, setOpen] = useState<number | null>(null)
  const logs = useAuditLogs(membership?.organization_id, can.admin, pages[pages.length - 1])

  if (!can.admin) {
    return (
      <GlassCard>
        <EmptyState icon={<ScrollText className="size-5" />} title="Admins and owners can read the audit log" />
      </GlassCard>
    )
  }

  return (
    <GlassCard className="overflow-hidden">
      <div className="px-5 pt-5 pb-3">
        <h2 className="text-sm font-semibold">Audit log</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Changes made in {membership?.organization_name}: members and roles, projects, API keys, workflows, schedules,
          and task retries, cancellations and archives. Account events such as sign-ins are kept per user, not per
          organization. Secrets are never recorded.
        </p>
      </div>
      {logs.isError ? (
        <ErrorState error={logs.error} onRetry={() => void logs.refetch()} />
      ) : logs.isPending ? (
        <RowsSkeleton rows={6} />
      ) : !logs.data.items.length ? (
        <EmptyState title="No entries yet" />
      ) : (
        <div className={cn("glass-strong overflow-x-auto transition-opacity", logs.isFetching && "opacity-70")}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">When</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Resource</TableHead>
                <TableHead>IP</TableHead>
                <TableHead className="pr-4">
                  <span className="sr-only">Details</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.data.items.map((entry) => {
                const hasDetails = Object.keys(entry.details ?? {}).length > 0
                return (
                  <Fragment key={entry.id}>
                    <TableRow>
                      <TableCell className="pl-4 whitespace-nowrap text-muted-foreground">{dateTime(entry.created_at)}</TableCell>
                      <TableCell>
                        <span className="block max-w-56 truncate">{entry.actor_label ?? entry.actor_type}</span>
                        <span className="text-xs text-muted-foreground">{entry.actor_type.replace("_", " ")}</span>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                      <TableCell className="text-xs">
                        {entry.resource_type}
                        {entry.resource_id && (
                          <span className="ml-1 font-mono text-muted-foreground" title={entry.resource_id}>
                            {entry.resource_id.slice(0, 8)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{entry.ip ?? "—"}</TableCell>
                      <TableCell className="pr-4 text-right">
                        {hasDetails && (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-expanded={open === entry.id}
                            onClick={() => setOpen(open === entry.id ? null : entry.id)}
                          >
                            Details <ChevronDown className={cn("transition-transform", open === entry.id && "rotate-180")} />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                    {open === entry.id && (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={6} className="px-4 pb-4">
                          <JsonView value={entry.details} maxHeight="14rem" />
                          {entry.request_id && <p className="mt-1 font-mono text-[11px] text-muted-foreground">request {entry.request_id}</p>}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
      {logs.data && (pages.length > 1 || logs.data.next_before_id != null) && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm">
          <span className="text-muted-foreground">Page {pages.length}</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={pages.length <= 1} onClick={() => setPages(pages.slice(0, -1))}>
              <ChevronLeft /> Newer
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={logs.data.next_before_id == null}
              onClick={() => setPages([...pages, logs.data.next_before_id ?? undefined])}
            >
              Older <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </GlassCard>
  )
}
