import type { Metadata } from "next"

import { WorkflowBuilderRoute } from "@/components/workflows/workflow-builder"

export const metadata: Metadata = { title: "Workflow builder" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <WorkflowBuilderRoute mode="edit" />
}
