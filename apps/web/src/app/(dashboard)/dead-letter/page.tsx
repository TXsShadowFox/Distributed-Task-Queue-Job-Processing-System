import type { Metadata } from "next"

import { DeadLetterPage } from "@/components/dead-letter/dead-letter-page"

export const metadata: Metadata = { title: "Dead letter queue" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <DeadLetterPage />
}
