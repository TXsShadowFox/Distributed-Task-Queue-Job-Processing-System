"use client"

import { Hash, Moon, PlusCircle, Sun, Workflow } from "lucide-react"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

import { NAV_ITEMS } from "@/components/layout/nav"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { setThemePreference, useResolvedTheme } from "@/lib/theme"
import { useUi } from "@/lib/ui-store"

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function CommandPalette() {
  const open = useUi((s) => s.commandOpen)
  const setOpen = useUi((s) => s.setCommand)
  const openSubmit = useUi((s) => s.openSubmitTask)
  const router = useRouter()
  const theme = useResolvedTheme()
  const [query, setQuery] = useState("")

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen(!useUi.getState().commandOpen)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [setOpen])

  const run = (fn: () => void) => {
    setOpen(false)
    setQuery("")
    fn()
  }
  const trimmed = query.trim()

  return (
    <CommandDialog open={open} onOpenChange={setOpen} title="Command palette" description="Search pages, tasks and actions">
      <CommandInput placeholder="Type a page, an action, or paste a task ID…" value={query} onValueChange={setQuery} />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>
        {trimmed.length >= 4 && (
          <CommandGroup heading="Tasks">
            <CommandItem
              value={`search-task ${trimmed}`}
              onSelect={() =>
                run(() =>
                  router.push(UUID_RE.test(trimmed) ? `/tasks/${trimmed}` : `/tasks?q=${encodeURIComponent(trimmed)}`),
                )
              }
            >
              <Hash />
              {UUID_RE.test(trimmed) ? "Open task " : "Search tasks for "}
              <span className="font-mono text-xs">{trimmed}</span>
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading="Actions">
          <CommandItem onSelect={() => run(() => openSubmit())}>
            <PlusCircle /> Submit a task
          </CommandItem>
          <CommandItem onSelect={() => run(() => router.push("/workflows?new=1"))}>
            <Workflow /> New workflow
          </CommandItem>
          <CommandItem onSelect={() => run(() => setThemePreference(theme === "dark" ? "light" : "dark"))}>
            {theme === "dark" ? <Sun /> : <Moon />} Switch to {theme === "dark" ? "light" : "dark"} glass
          </CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Go to">
          {NAV_ITEMS.map(({ href, label, icon: Icon, description }) => (
            <CommandItem key={href} value={`${label} ${description}`} onSelect={() => run(() => router.push(href))}>
              <Icon />
              <span>{label}</span>
              <span className="ml-auto truncate text-xs text-muted-foreground">{description}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
