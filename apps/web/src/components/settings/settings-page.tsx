"use client"

import { usePathname, useSearchParams } from "next/navigation"

import { PageHeader } from "@/components/common/primitives"
import { AccountSettings } from "@/components/settings/account-settings"
import { AuditLog } from "@/components/settings/audit-log"
import { OrganizationSettings } from "@/components/settings/organization-settings"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { replaceQuery } from "@/lib/url-state"

const TABS = ["account", "organization", "audit"] as const
type Tab = (typeof TABS)[number]

export function SettingsPage() {
  const params = useSearchParams()
  const pathname = usePathname()
  const requested = params.get("tab") as Tab | null
  const tab: Tab = requested && TABS.includes(requested) ? requested : "account"

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Your account, your organization and its audit trail." />
      <Tabs value={tab} onValueChange={(v) => replaceQuery(pathname, v === "account" ? "" : `tab=${v}`)}>
        <TabsList>
          <TabsTrigger value="account">Account</TabsTrigger>
          <TabsTrigger value="organization">Organization</TabsTrigger>
          <TabsTrigger value="audit">Audit log</TabsTrigger>
        </TabsList>
        <TabsContent value="account" className="mt-4">
          <AccountSettings />
        </TabsContent>
        <TabsContent value="organization" className="mt-4">
          <OrganizationSettings />
        </TabsContent>
        <TabsContent value="audit" className="mt-4">
          <AuditLog />
        </TabsContent>
      </Tabs>
    </div>
  )
}
