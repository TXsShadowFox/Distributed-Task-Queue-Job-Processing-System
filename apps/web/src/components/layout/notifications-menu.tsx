"use client"

import { AlertOctagon, Bell, CheckCheck, ServerCrash, Workflow } from "lucide-react"
import Link from "next/link"

import { RelativeTime } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useMarkNotificationsRead, useNotifications } from "@/lib/api/hooks"
import { cn } from "@/lib/utils"

const ICONS = { dead_letter: AlertOctagon, workflow_failed: Workflow, worker_offline: ServerCrash } as const

export function NotificationsMenu() {
  const { data, isLoading } = useNotifications()
  const markRead = useMarkNotificationsRead()
  const unread = data?.unread_count ?? 0

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}>
          <Bell />
          {unread > 0 && (
            <span className="absolute top-1 right-1 flex min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] leading-4 font-semibold text-white">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[22rem] p-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <p className="text-sm font-medium">Notifications</p>
          <Button variant="ghost" size="xs" disabled={!unread || markRead.isPending} onClick={() => markRead.mutate()}>
            <CheckCheck /> Mark all read
          </Button>
        </div>
        <ScrollArea className="max-h-96">
          {isLoading ? (
            <p className="p-4 text-sm text-muted-foreground">Loading…</p>
          ) : !data?.items.length ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              You&apos;re all caught up. Dead-lettered tasks, failed workflow runs and offline workers appear here.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {data.items.map((n) => {
                const Icon = ICONS[n.category as keyof typeof ICONS] ?? Bell
                return (
                  <li key={n.id}>
                    <Link href={n.link} className={cn("flex gap-3 px-4 py-3 text-sm hover:bg-muted", n.unread && "bg-primary/5")}>
                      <Icon className={cn("mt-0.5 size-4 shrink-0", n.severity === "error" ? "text-destructive" : "text-warning")} aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{n.title}</span>
                        {n.detail && <span className="line-clamp-2 block text-xs text-muted-foreground">{n.detail}</span>}
                        <RelativeTime value={n.ts} className="text-xs text-muted-foreground" />
                      </span>
                      {n.unread && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label="unread" />}
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
