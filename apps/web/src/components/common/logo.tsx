import { cn } from "@/lib/utils"

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8", className)} aria-hidden>
      <defs>
        <linearGradient id="ff-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="0.55" stopColor="#6366f1" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#ff-g)" opacity="0.18" />
      <rect x="1" y="1" width="30" height="30" rx="9" fill="none" stroke="url(#ff-g)" strokeOpacity="0.6" />
      <path d="M9 22V10h11" stroke="url(#ff-g)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <path d="M9 16h8" stroke="url(#ff-g)" strokeWidth="2.6" strokeLinecap="round" fill="none" />
      <circle cx="23" cy="21" r="3" fill="#22d3ee" />
    </svg>
  )
}

export function Logo({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      {!collapsed && (
        <span className="leading-tight">
          <span className="block text-sm font-semibold tracking-tight">FlowForge</span>
          <span className="block text-[10px] tracking-wide text-muted-foreground uppercase">Orchestrate · Execute · Observe</span>
        </span>
      )}
    </span>
  )
}
