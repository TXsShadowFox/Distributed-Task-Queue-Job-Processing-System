import type { Metadata } from "next"

import { ApiKeysPage } from "@/components/api-keys/api-keys-page"

export const metadata: Metadata = { title: "API keys" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <ApiKeysPage />
}
