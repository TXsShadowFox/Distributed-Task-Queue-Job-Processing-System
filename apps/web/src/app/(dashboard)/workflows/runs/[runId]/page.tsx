import type { Metadata } from "next"

import { WorkflowRunPage } from "@/components/workflows/workflow-run-page"

export const metadata: Metadata = { title: "Workflow run" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <WorkflowRunPage />
}
