"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { GlassCard } from "@/components/common/primitives"
import { Logo } from "@/components/common/logo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, authPost, errorMessage } from "@/lib/api/client"
import type { Me, TokenOut } from "@/lib/api/types"
import { useSession } from "@/lib/session"

const loginSchema = z.object({
  email: z.email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
})

const registerSchema = z
  .object({
    full_name: z.string().trim().min(1, "Your name is required").max(120),
    email: z.email("Enter a valid email address"),
    organization_name: z.string().trim().max(120).optional(),
    password: z.string().min(10, "Use at least 10 characters").max(128),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" })

/** Only allow same-site relative redirects after login (no open redirects). */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/overview"
}

async function completeSignIn(token: TokenOut) {
  const session = useSession.getState()
  session.setToken(token)
  const me = await api<Me>("/auth/me", { project: false })
  session.setMe(me)
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return (
    <p id={id} className="text-xs text-destructive" role="alert">
      {message}
    </p>
  )
}

export function LoginForm() {
  const router = useRouter()
  const next = safeNext(useSearchParams().get("next"))
  const [serverError, setServerError] = useState<string | null>(null)
  const form = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } })

  const onSubmit = form.handleSubmit(async (values) => {
    setServerError(null)
    try {
      await completeSignIn(await authPost<TokenOut>("/auth/login", values))
      router.replace(next)
    } catch (error) {
      setServerError(errorMessage(error))
    }
  })

  const { errors, isSubmitting } = form.formState
  return (
    <GlassCard className="p-6 sm:p-8">
      <div className="lg:hidden">
        <Logo />
      </div>
      <h2 className="mt-6 text-xl font-semibold tracking-tight lg:mt-0">Sign in</h2>
      <p className="mt-1 text-sm text-muted-foreground">Welcome back. Use your FlowForge account.</p>
      <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} aria-describedby="email-error" {...form.register("email")} />
          <FieldError id="email-error" message={errors.email?.message} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!errors.password} aria-describedby="password-error" {...form.register("password")} />
          <FieldError id="password-error" message={errors.password?.message} />
        </div>
        {serverError && (
          <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {serverError}
          </p>
        )}
        <Button type="submit" className="h-10 w-full" disabled={isSubmitting}>
          {isSubmitting && <Loader2 className="animate-spin" />} Sign in
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted-foreground">
        New here?{" "}
        <Link href="/register" className="font-medium text-primary underline-offset-4 hover:underline">
          Create an account
        </Link>
      </p>
    </GlassCard>
  )
}

export function RegisterForm() {
  const router = useRouter()
  const [serverError, setServerError] = useState<string | null>(null)
  const form = useForm<z.infer<typeof registerSchema>>({
    resolver: zodResolver(registerSchema),
    defaultValues: { full_name: "", email: "", organization_name: "", password: "", confirm: "" },
  })

  const onSubmit = form.handleSubmit(async ({ full_name, email, password, organization_name }) => {
    setServerError(null)
    try {
      const token = await authPost<TokenOut>("/auth/register", {
        full_name,
        email,
        password,
        organization_name: organization_name || undefined,
      })
      await completeSignIn(token)
      router.replace("/overview")
    } catch (error) {
      setServerError(errorMessage(error))
    }
  })

  const { errors, isSubmitting } = form.formState
  const field = (name: keyof z.infer<typeof registerSchema>, label: string, props: React.ComponentProps<"input"> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={name}>{label}</Label>
      <Input id={name} aria-invalid={!!errors[name]} aria-describedby={`${name}-error`} {...props} {...form.register(name)} />
      <FieldError id={`${name}-error`} message={errors[name]?.message} />
    </div>
  )

  return (
    <GlassCard className="p-6 sm:p-8">
      <div className="lg:hidden">
        <Logo />
      </div>
      <h2 className="mt-6 text-xl font-semibold tracking-tight lg:mt-0">Create your account</h2>
      <p className="mt-1 text-sm text-muted-foreground">You get an organization and a default project.</p>
      <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
        {field("full_name", "Full name", { autoComplete: "name" })}
        {field("email", "Email", { type: "email", autoComplete: "email" })}
        {field("organization_name", "Organization (optional)", { autoComplete: "organization", placeholder: "Acme Inc." })}
        {field("password", "Password", { type: "password", autoComplete: "new-password" })}
        {field("confirm", "Confirm password", { type: "password", autoComplete: "new-password" })}
        {serverError && (
          <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {serverError}
          </p>
        )}
        <Button type="submit" className="h-10 w-full" disabled={isSubmitting}>
          {isSubmitting && <Loader2 className="animate-spin" />} Create account
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </GlassCard>
  )
}
