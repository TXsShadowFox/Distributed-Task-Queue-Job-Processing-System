import type { Metadata } from "next"

import { OverviewPage } from "@/components/overview/overview-page"

export const metadata: Metadata = { title: "Overview" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <OverviewPage />
}
