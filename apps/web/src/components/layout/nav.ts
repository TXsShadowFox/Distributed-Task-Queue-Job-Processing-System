import {
  ArchiveX,
  BarChart3,
  CalendarClock,
  Cpu,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Settings,
  Workflow,
  type LucideIcon,
} from "lucide-react"

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard, description: "Live health, throughput and activity" },
  { href: "/tasks", label: "Tasks", icon: ListChecks, description: "Search, inspect, submit, retry and cancel" },
  { href: "/workflows", label: "Workflows", icon: Workflow, description: "Build and run DAG workflows" },
  { href: "/workers", label: "Workers", icon: Cpu, description: "Fleet status, heartbeats and draining" },
  { href: "/schedules", label: "Schedules", icon: CalendarClock, description: "Cron schedules and delayed jobs" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, description: "Throughput, latency and failures" },
  { href: "/dead-letter", label: "Dead Letter Queue", icon: ArchiveX, description: "Inspect, retry or archive failures" },
  { href: "/api-keys", label: "API Keys", icon: KeyRound, description: "Scoped keys for the API and SDK" },
  { href: "/settings", label: "Settings", icon: Settings, description: "Profile, organization and preferences" },
]

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`)
}
