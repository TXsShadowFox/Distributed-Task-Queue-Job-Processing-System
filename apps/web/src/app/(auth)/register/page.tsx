import type { Metadata } from "next"
import { Suspense } from "react"

import { RegisterForm } from "@/components/auth/auth-forms"
import { GlassCard } from "@/components/common/primitives"

export const metadata: Metadata = { title: "Create account" }

export default function RegisterPage() {
  return (
    <Suspense fallback={<GlassCard className="h-[34rem] animate-pulse" />}>
      <RegisterForm />
    </Suspense>
  )
}
