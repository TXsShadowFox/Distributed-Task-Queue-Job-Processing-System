import type { Metadata } from "next"

import { SettingsPage } from "@/components/settings/settings-page"

export const metadata: Metadata = { title: "Settings" }
// Browser-only authenticated page (see app/(dashboard)/layout.tsx).
export const instant = false

export default function Page() {
  return <SettingsPage />
}
