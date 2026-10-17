import type { Metadata } from "next"

import { WorkflowsPage } from "@/components/workflows/workflows-page"

export const metadata: Metadata = { title: "Workflows" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <WorkflowsPage />
}
