import type { Metadata } from "next"
import { Suspense } from "react"

import { LoginForm } from "@/components/auth/auth-forms"
import { GlassCard } from "@/components/common/primitives"

export const metadata: Metadata = { title: "Sign in" }

export default function LoginPage() {
  return (
    <Suspense fallback={<GlassCard className="h-[26rem] animate-pulse" />}>
      <LoginForm />
    </Suspense>
  )
}
