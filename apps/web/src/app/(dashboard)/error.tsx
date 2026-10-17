"use client"

import { AlertTriangle, RotateCcw } from "lucide-react"
import Link from "next/link"
import { useEffect } from "react"

import { GlassCard } from "@/components/common/primitives"
import { Button } from "@/components/ui/button"

/** Catches render errors in a dashboard page; the shell (navigation, project switcher) stays usable. */
export default function DashboardError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error("[dashboard] page crashed", error)
  }, [error])

  return (
    <GlassCard className="mx-auto mt-12 max-w-lg p-8 text-center" role="alert">
      <AlertTriangle className="mx-auto size-8 text-destructive" aria-hidden />
      <h1 className="mt-4 text-lg font-semibold">This page hit an unexpected error</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Your data is safe: nothing was changed. Try again, or go back to the overview.
      </p>
      {error.digest && <p className="mt-2 font-mono text-xs text-muted-foreground">Reference {error.digest}</p>}
      <div className="mt-6 flex justify-center gap-2">
        <Button onClick={() => retry()}>
          <RotateCcw /> Try again
        </Button>
        <Button variant="outline" asChild>
          <Link href="/overview">Overview</Link>
        </Button>
      </div>
    </GlassCard>
  )
}
