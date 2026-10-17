import { describe, expect, it } from "vitest"

import { CRON_PRESETS, formatLocalFireTime } from "./cron-field"

describe("formatLocalFireTime", () => {
  it("keeps the server's wall-clock time and zone abbreviation", () => {
    const day = new Date(Date.UTC(2026, 9, 12)).toLocaleDateString(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    })
    // The time must not be shifted into the viewer's zone: 02:30 EDT stays 02:30.
    expect(formatLocalFireTime("2026-10-12 02:30 EDT")).toBe(`${day}, 02:30 EDT`)
    expect(formatLocalFireTime("2026-10-12 02:30")).toBe(`${day}, 02:30`)
  })

  it("passes unexpected text through", () => {
    expect(formatLocalFireTime("soon")).toBe("soon")
  })
})

describe("CRON_PRESETS", () => {
  it("are five-field expressions with unique values", () => {
    for (const preset of CRON_PRESETS) expect(preset.expression.split(" ")).toHaveLength(5)
    expect(new Set(CRON_PRESETS.map((p) => p.expression)).size).toBe(CRON_PRESETS.length)
  })
})
