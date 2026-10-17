import { Suspense, type ReactNode } from "react"

import { AppShell } from "@/components/layout/app-shell"
import { AuthGate, ShellSkeleton } from "@/components/layout/auth-gate"

/**
 * The authenticated dashboard renders only in the browser (see AuthGate), so the framework cannot render its pages at
 * prerender time to validate instant navigation. Opt this subtree out of that validation; client navigations between
 * dashboard pages are still instant because every page is a Client Component fed by the client-side query cache.
 * Placed as low as possible so the public routes (/login, /register) keep being validated.
 */
export const instant = false

/**
 * The dashboard is authenticated, client-rendered UI. Everything below this boundary reads URL state and the
 * in-memory session, so the prerendered HTML is the skeleton fallback (Cache Components requires URL hooks such as
 * usePathname/useSearchParams to sit inside <Suspense>).
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<ShellSkeleton />}>
      <AuthGate>
        <AppShell>{children}</AppShell>
      </AuthGate>
    </Suspense>
  )
}
