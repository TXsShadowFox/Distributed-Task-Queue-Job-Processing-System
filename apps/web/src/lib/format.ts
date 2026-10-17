const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
]

export function relativeTime(value: string | number | Date | null | undefined, now = Date.now()): string {
  if (value === null || value === undefined) return "—"
  const ts = typeof value === "number" ? value : new Date(value).getTime()
  if (Number.isNaN(ts)) return "—"
  const diff = (ts - now) / 1000
  if (Math.abs(diff) < 5) return "just now"
  for (const [unit, seconds] of UNITS) {
    if (Math.abs(diff) >= seconds || unit === "second") {
      return rtf.format(Math.round(diff / seconds), unit)
    }
  }
  return "—"
}

export function dateTime(value: string | null | undefined, timeZone?: string): string {
  if (!value) return "—"
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone,
  }).format(new Date(value))
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—"
  if (ms === 0) return "0 ms"
  if (ms < 1) return "<1 ms"
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 59_950) {
    const s = ms / 1000
    return `${s.toFixed(s < 10 ? 2 : 1)} s`
  }
  const total = Math.round(ms / 1000)
  const m = Math.floor(total / 60)
  if (m < 60) return total % 60 ? `${m}m ${total % 60}s` : `${m}m`
  const h = Math.floor(m / 60)
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`
}

export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—"
  return `${(value * 100).toFixed(digits)}%`
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  const units = ["B", "KB", "MB", "GB", "TB"]
  let v = value
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function shortId(id: string | null | undefined): string {
  if (!id) return "—"
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ")
}
