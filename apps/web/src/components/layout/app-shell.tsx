"use client"

import { Menu, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import type { ReactNode } from "react"

import { Logo } from "@/components/common/logo"
import { CommandPalette } from "@/components/layout/command-palette"
import { isActive, NAV_ITEMS } from "@/components/layout/nav"
import { NotificationsMenu } from "@/components/layout/notifications-menu"
import { ProjectSwitcher } from "@/components/layout/project-switcher"
import { SystemStatusPill } from "@/components/layout/system-status"
import { ThemeMenu } from "@/components/layout/theme-menu"
import { UserMenu } from "@/components/layout/user-menu"
import { SubmitTaskDialog } from "@/components/tasks/submit-task-dialog"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useRealtime, useRealtimeConnection } from "@/lib/realtime"
import { useUi } from "@/lib/ui-store"
import { cn } from "@/lib/utils"

function NavLinks({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname()
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const active = isActive(pathname, href)
        const link = (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group flex h-10 items-center gap-3 rounded-xl px-3 text-sm font-medium text-muted-foreground transition-colors",
              "hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-2",
              active && "bg-sidebar-accent text-foreground shadow-[inset_2px_0_0_var(--primary)]",
              collapsed && "justify-center px-0",
            )}
          >
            <Icon className={cn("size-[18px] shrink-0", active && "text-primary")} aria-hidden />
            {!collapsed && <span className="truncate">{label}</span>}
            {collapsed && <span className="sr-only">{label}</span>}
          </Link>
        )
        if (!collapsed) return link
        return (
          <Tooltip key={href}>
            <TooltipTrigger asChild>{link}</TooltipTrigger>
            <TooltipContent side="right">{label}</TooltipContent>
          </Tooltip>
        )
      })}
    </nav>
  )
}

function RealtimeIndicator({ collapsed }: { collapsed: boolean }) {
  const status = useRealtime((s) => s.status)
  const color = { live: "bg-success", connecting: "bg-warning", reconnecting: "bg-warning", offline: "bg-destructive" }[status]
  const label = { live: "Live updates", connecting: "Connecting…", reconnecting: "Reconnecting…", offline: "Live updates offline" }[status]
  return (
    <div className={cn("flex items-center gap-2 px-3 text-xs text-muted-foreground", collapsed && "justify-center px-0")} title={label}>
      <span className="relative flex size-2">
        {status === "live" && <span className={cn("absolute size-full animate-ping rounded-full opacity-50", color)} />}
        <span className={cn("relative size-2 rounded-full", color)} />
      </span>
      {!collapsed && <span>{label}</span>}
    </div>
  )
}

function Sidebar() {
  const collapsed = useUi((s) => s.sidebarCollapsed)
  const toggle = useUi((s) => s.toggleSidebar)
  return (
    <aside
      className={cn(
        "glass sticky top-3 z-30 m-3 hidden h-[calc(100dvh-1.5rem)] shrink-0 flex-col rounded-2xl bg-sidebar p-3 transition-[width] duration-200 lg:flex",
        collapsed ? "w-[4.5rem]" : "w-64",
      )}
    >
      <div className={cn("flex h-12 items-center px-1", collapsed && "justify-center")}>
        <Link href="/overview" aria-label="FlowForge overview">
          <Logo collapsed={collapsed} />
        </Link>
      </div>
      <div className="mt-4 flex-1 overflow-y-auto">
        <NavLinks collapsed={collapsed} />
      </div>
      <div className="space-y-3 border-t border-sidebar-border pt-3">
        <RealtimeIndicator collapsed={collapsed} />
        <Button
          variant="ghost"
          size={collapsed ? "icon" : "default"}
          className={cn("w-full text-muted-foreground", !collapsed && "justify-start")}
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          {!collapsed && "Collapse"}
        </Button>
      </div>
    </aside>
  )
}

function MobileNav() {
  const open = useUi((s) => s.mobileNavOpen)
  const setOpen = useUi((s) => s.setMobileNav)
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent side="left" className="glass w-72 p-4">
        <SheetHeader className="p-0">
          <SheetTitle>
            <Logo />
          </SheetTitle>
        </SheetHeader>
        <div className="mt-6">
          <NavLinks collapsed={false} onNavigate={() => setOpen(false)} />
        </div>
        <div className="mt-auto">
          <RealtimeIndicator collapsed={false} />
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Topbar() {
  const setMobileNav = useUi((s) => s.setMobileNav)
  const setCommand = useUi((s) => s.setCommand)
  return (
    <header className="glass sticky top-3 z-20 mx-3 mt-3 flex h-14 items-center gap-2 rounded-2xl px-3 lg:ml-0">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open navigation">
        <Menu />
      </Button>
      <button
        type="button"
        onClick={() => setCommand(true)}
        className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-xl border border-border bg-muted/50 px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted sm:max-w-sm"
        aria-label="Search tasks and pages"
      >
        <Search className="size-4 shrink-0" aria-hidden />
        <span className="truncate">Search tasks, pages, actions…</span>
        <kbd className="ml-auto hidden rounded border border-border px-1.5 font-mono text-[10px] sm:inline">Ctrl K</kbd>
      </button>
      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        <ProjectSwitcher />
        <SystemStatusPill />
        <NotificationsMenu />
        <ThemeMenu />
        <UserMenu />
      </div>
    </header>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  useRealtimeConnection()
  return (
    <div className="flex min-h-dvh">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main id="main" className="mx-auto w-full max-w-[1600px] flex-1 px-3 py-6 sm:px-5 lg:pr-3 lg:pl-0">
          {children}
        </main>
      </div>
      <MobileNav />
      <CommandPalette />
      <SubmitTaskDialog />
    </div>
  )
}
