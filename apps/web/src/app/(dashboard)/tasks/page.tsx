import type { Metadata } from "next"

import { TasksPage } from "@/components/tasks/tasks-page"

export const metadata: Metadata = { title: "Tasks" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <TasksPage />
}
