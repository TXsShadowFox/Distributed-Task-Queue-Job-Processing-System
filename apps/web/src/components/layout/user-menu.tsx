"use client"

import { useQueryClient } from "@tanstack/react-query"
import { BookOpen, LogOut, Settings, ShieldCheck } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { toast } from "sonner"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { API_BASE, authPost, errorMessage } from "@/lib/api/client"
import { useCurrentMembership, useSession } from "@/lib/session"
import { useRealtime } from "@/lib/realtime"

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("")
}

export function UserMenu() {
  const user = useSession((s) => s.user)
  const membership = useCurrentMembership()
  const queryClient = useQueryClient()
  const router = useRouter()

  async function logout() {
    try {
      await authPost("/auth/logout")
    } catch (error) {
      toast.error(`Sign-out request failed: ${errorMessage(error)}`)
    }
    queryClient.clear()
    useRealtime.getState().clear()
    useSession.getState().setAnonymous()
    router.replace("/login")
  }

  if (!user) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="rounded-full" aria-label="Account menu">
          <Avatar className="size-8">
            <AvatarFallback className="bg-gradient-to-br from-cyan-500/30 to-violet-500/30 text-xs font-semibold">
              {initials(user.full_name) || "?"}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>
          <p className="truncate text-sm font-medium">{user.full_name}</p>
          <p className="truncate text-xs font-normal text-muted-foreground">{user.email}</p>
          {membership && (
            <p className="mt-1 text-[10px] tracking-wide text-muted-foreground uppercase">
              {membership.role} · {membership.organization_name}
            </p>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings /> Settings
          </Link>
        </DropdownMenuItem>
        {user.is_platform_admin && (
          <DropdownMenuItem disabled>
            <ShieldCheck /> Platform administrator
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <a href={`${API_BASE.replace(/\/api\/v1$/, "")}/docs`} target="_blank" rel="noreferrer">
            <BookOpen /> API reference
          </a>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()} variant="destructive">
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
