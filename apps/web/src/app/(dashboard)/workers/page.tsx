import type { Metadata } from "next"

import { WorkersPage } from "@/components/workers/workers-page"

export const metadata: Metadata = { title: "Workers" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <WorkersPage />
}
