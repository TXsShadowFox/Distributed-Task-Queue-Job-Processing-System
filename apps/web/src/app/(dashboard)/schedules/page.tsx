import type { Metadata } from "next"

import { SchedulesPage } from "@/components/schedules/schedules-page"

export const metadata: Metadata = { title: "Schedules" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <SchedulesPage />
}
