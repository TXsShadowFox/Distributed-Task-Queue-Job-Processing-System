import type { Metadata } from "next"
import Link from "next/link"

import { LogoMark } from "@/components/common/logo"
import { Button } from "@/components/ui/button"

export const metadata: Metadata = { title: "Not found" }

export default function NotFound() {
  return (
    <main id="main" className="flex min-h-dvh items-center justify-center p-4">
      <div className="glass-strong w-full max-w-md rounded-2xl p-8 text-center">
        <LogoMark className="mx-auto size-10" />
        <p className="mt-6 font-mono text-sm text-muted-foreground">404</p>
        <h1 className="mt-1 text-xl font-semibold">This page does not exist</h1>
        <p className="mt-2 text-sm text-muted-foreground">The link may be mistyped, or the page was moved.</p>
        <Button asChild className="mt-6">
          <Link href="/overview">Go to the overview</Link>
        </Button>
      </div>
    </main>
  )
}
