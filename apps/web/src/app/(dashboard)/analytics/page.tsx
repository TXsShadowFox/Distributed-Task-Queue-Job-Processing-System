import type { Metadata } from "next"

import { AnalyticsPage } from "@/components/analytics/analytics-page"

export const metadata: Metadata = { title: "Analytics" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <AnalyticsPage />
}
