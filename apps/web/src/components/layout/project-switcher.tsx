"use client"

import { useQueryClient } from "@tanstack/react-query"
import { Check, ChevronsUpDown, FolderKanban } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useCurrentProject, useSession } from "@/lib/session"

export function ProjectSwitcher() {
  const memberships = useSession((s) => s.memberships)
  const projectId = useSession((s) => s.projectId)
  const setProject = useSession((s) => s.setProject)
  const current = useCurrentProject()
  const queryClient = useQueryClient()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="h-9 max-w-[11rem] gap-2 sm:max-w-[14rem]" aria-label="Switch project">
          <FolderKanban className="text-primary" />
          <span className="truncate">{current?.name ?? "Select project"}</span>
          <ChevronsUpDown className="ml-auto opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        {memberships.map((m, i) => (
          <DropdownMenuGroup key={m.organization_id}>
            {i > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="flex items-center justify-between">
              <span className="truncate">{m.organization_name}</span>
              <span className="text-[10px] tracking-wide text-muted-foreground uppercase">{m.role}</span>
            </DropdownMenuLabel>
            {m.projects.map((p) => (
              <DropdownMenuItem
                key={p.id}
                onSelect={() => {
                  if (p.id === projectId) return
                  setProject(p.id)
                  void queryClient.invalidateQueries({ queryKey: ["p"] })
                }}
              >
                <span className="truncate">{p.name}</span>
                {p.id === projectId && <Check className="ml-auto text-primary" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
