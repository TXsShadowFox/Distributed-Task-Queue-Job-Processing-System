"use client"

import { Check, ChevronsUpDown, Globe } from "lucide-react"
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

let zones: string[] | null = null

/** IANA zone names the browser knows, UTC first. The server re-validates the choice against its own tz database. */
export function timeZones(): string[] {
  if (!zones) {
    const supported = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : []
    zones = ["UTC", ...supported.filter((z) => z !== "UTC")]
  }
  return zones
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

export function TimezonePicker({
  id,
  value,
  onChange,
  disabled,
}: {
  id?: string
  value: string
  onChange: (zone: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const all = useMemo(() => timeZones(), [])
  const local = browserTimeZone()
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full justify-between font-normal"
        >
          <span className="flex min-w-0 items-center gap-2">
            <Globe className="size-4 text-muted-foreground" aria-hidden />
            <span className="truncate">{value}</span>
          </span>
          <ChevronsUpDown className="opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) min-w-64 p-0" align="start">
        <Command>
          <CommandInput placeholder="Search time zones…" />
          <CommandList>
            <CommandEmpty>No time zone found.</CommandEmpty>
            {local !== "UTC" && (
              <CommandGroup heading="Your browser">
                <ZoneItem zone={local} itemValue={`browser ${local}`} selected={value === local} onSelect={(z) => (onChange(z), setOpen(false))} />
              </CommandGroup>
            )}
            <CommandGroup heading="All time zones">
              {all.map((zone) => (
                <ZoneItem key={zone} zone={zone} selected={value === zone} onSelect={(z) => (onChange(z), setOpen(false))} />
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function ZoneItem({
  zone,
  itemValue = zone,
  selected,
  onSelect,
}: {
  zone: string
  /** cmdk identifies items by value, so a zone listed twice needs a distinct one. */
  itemValue?: string
  selected: boolean
  onSelect: (zone: string) => void
}) {
  return (
    <CommandItem value={itemValue} onSelect={() => onSelect(zone)}>
      <Check className={cn("size-4", selected ? "opacity-100" : "opacity-0")} aria-hidden />
      {zone.replace(/_/g, " ")}
    </CommandItem>
  )
}
