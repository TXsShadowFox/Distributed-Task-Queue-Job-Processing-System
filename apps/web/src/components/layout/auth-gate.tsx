"use client"

import { usePathname, useRouter } from "next/navigation"
import { use, useEffect, type ReactNode } from "react"
import { browser } from "react-dom"

import { LogoMark } from "@/components/common/logo"
import { api, refreshSession } from "@/lib/api/client"
import type { Me } from "@/lib/api/types"
import { useSession } from "@/lib/session"

export function ShellSkeleton() {
  return (
    <div className="flex min-h-dvh items-center justify-center" aria-busy="true" aria-label="Loading FlowForge">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <LogoMark className="size-10 animate-pulse" />
        <span className="text-sm">Loading your workspace…</span>
      </div>
    </div>
  )
}

let bootstrapping: Promise<void> | null = null

/** Restore the session from the httpOnly refresh cookie, then load the user's memberships. */
function bootstrap(): Promise<void> {
  bootstrapping ??= (async () => {
    const session = useSession.getState()
    try {
      if (!session.accessToken && !(await refreshSession())) {
        session.setAnonymous()
        return
      }
      session.setMe(await api<Me>("/auth/me", { project: false }))
    } catch {
      useSession.getState().setAnonymous()
    } finally {
      bootstrapping = null
    }
  })()
  return bootstrapping
}

export function AuthGate({ children }: { children: ReactNode }) {
  // The dashboard depends on the in-memory session, so it renders only in the browser: during prerendering this
  // suspends and the layout's <Suspense> fallback becomes the static shell (no server/client hydration mismatch).
  use(browser("The FlowForge dashboard depends on the in-memory session, which exists only in the browser."))
  const status = useSession((s) => s.status)
  const expiresAt = useSession((s) => s.expiresAt)
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (status === "unknown") void bootstrap()
  }, [status])

  useEffect(() => {
    if (status === "anonymous") router.replace(`/login?next=${encodeURIComponent(pathname)}`)
  }, [status, pathname, router])

  // Refresh the access token a minute before it expires so requests never race the expiry.
  useEffect(() => {
    if (status !== "authenticated" || !expiresAt) return
    const delay = Math.max(5_000, expiresAt - Date.now() - 60_000)
    const timer = setTimeout(() => {
      void refreshSession().then((ok) => {
        if (!ok) useSession.getState().setAnonymous()
      })
    }, delay)
    return () => clearTimeout(timer)
  }, [status, expiresAt])

  if (status !== "authenticated") return <ShellSkeleton />
  return <>{children}</>
}
