import type { Metadata } from "next"

import { TaskDetailPage } from "@/components/tasks/task-detail-page"

export const metadata: Metadata = { title: "Task" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <TaskDetailPage />
}
