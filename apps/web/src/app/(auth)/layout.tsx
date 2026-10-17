import { Activity, GitBranch, ShieldCheck } from "lucide-react"

import { Logo } from "@/components/common/logo"

const POINTS = [
  { icon: Activity, title: "At-least-once execution", text: "Leases, fencing tokens and an outbox keep every task accounted for." },
  { icon: GitBranch, title: "DAG workflows", text: "Compose tasks into versioned workflows with live per-node status." },
  { icon: ShieldCheck, title: "Secure by default", text: "Scoped API keys, RBAC and tenant isolation on every request." },
]

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="mx-auto grid min-h-dvh max-w-6xl items-center gap-10 px-4 py-10 lg:grid-cols-2 lg:px-8">
      <div className="hidden lg:block">
        <Logo />
        <h1 className="mt-10 text-4xl leading-tight font-semibold tracking-tight">
          Orchestrate. Execute. <span className="text-gradient">Observe.</span>
        </h1>
        <p className="mt-4 max-w-md text-muted-foreground">
          A distributed task platform with durable queues, retries, schedules and workflows — and a dashboard that
          shows exactly what is happening.
        </p>
        <ul className="mt-10 space-y-5">
          {POINTS.map(({ icon: Icon, title, text }) => (
            <li key={title} className="flex gap-3">
              <span className="glass flex size-9 shrink-0 items-center justify-center rounded-xl text-primary">
                <Icon className="size-4" aria-hidden />
              </span>
              <span>
                <span className="block text-sm font-medium">{title}</span>
                <span className="block text-sm text-muted-foreground">{text}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="mx-auto w-full max-w-md">{children}</div>
    </main>
  )
}
