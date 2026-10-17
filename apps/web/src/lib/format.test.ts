import { describe, expect, it } from "vitest"

import { bytes, duration, percent, relativeTime, shortId, titleCase } from "./format"

describe("duration", () => {
  it.each([
    [null, "—"],
    [undefined, "—"],
    [Number.NaN, "—"],
    [0, "0 ms"],
    [0.4, "<1 ms"],
    [12.6, "13 ms"],
    [999, "999 ms"],
    [1_234, "1.23 s"],
    [12_340, "12.3 s"],
    [59_940, "59.9 s"],
    [59_960, "1m"], // rounds up to a whole minute instead of printing "60.0 s"
    [61_000, "1m 1s"],
    [119_600, "2m"], // never "1m 60s"
    [3_600_000, "1h"],
    [3_660_000, "1h 1m"],
  ])("duration(%j) = %j", (ms, text) => {
    expect(duration(ms as number | null | undefined)).toBe(text)
  })
})

describe("relativeTime", () => {
  const now = Date.UTC(2026, 9, 13, 12, 0, 0)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })

  it("says just now within five seconds either way", () => {
    expect(relativeTime(now - 4_000, now)).toBe("just now")
    expect(relativeTime(now + 4_000, now)).toBe("just now")
  })

  it("picks the largest whole unit", () => {
    expect(relativeTime(now - 2 * 3_600_000, now)).toBe(rtf.format(-2, "hour"))
    expect(relativeTime(now + 90_000, now)).toBe(rtf.format(2, "minute"))
    expect(relativeTime(new Date(now - 3 * 86_400_000).toISOString(), now)).toBe(rtf.format(-3, "day"))
  })

  it("handles missing and invalid values", () => {
    expect(relativeTime(null, now)).toBe("—")
    expect(relativeTime("not a date", now)).toBe("—")
  })
})

describe("small formatters", () => {
  it("formats percentages from fractions", () => {
    expect(percent(0.857)).toBe("85.7%")
    expect(percent(0.0002, 2)).toBe("0.02%")
    expect(percent(null)).toBe("—")
  })

  it("formats byte sizes", () => {
    expect(bytes(512)).toBe("512 B")
    expect(bytes(23_300_000)).toBe("22.2 MB")
    expect(bytes(150 * 1024)).toBe("150 KB")
    expect(bytes(undefined)).toBe("—")
  })

  it("shortens ids and title-cases states", () => {
    expect(shortId("01a12a9d-3847-76fd-b279-840a41bfcb53")).toBe("01a12a9d…cb53")
    expect(shortId("short")).toBe("short")
    expect(titleCase("DEAD_LETTERED")).toBe("Dead Lettered")
    expect(titleCase("retry scheduled")).toBe("Retry Scheduled")
  })
})
