import { describe, expect, it } from "vitest"

import type { TaskLog } from "@/lib/api/types"

import { mergeLogLines } from "./task-logs"

const line = (id: number, message = `line ${id}`): TaskLog => ({
  id,
  attempt_number: 1,
  level: "INFO",
  message,
  fields: null,
  logged_at: "2026-10-13T12:00:00Z",
})

describe("mergeLogLines", () => {
  it("de-duplicates by id and keeps id order across sources", () => {
    const stored = [line(1), line(2), line(3)]
    // A reconnecting stream re-sends part of its backlog, out of order.
    const streamed = [line(4), line(2, "line 2 (replayed)"), line(5)]
    const merged = mergeLogLines(stored, streamed)
    expect(merged.map((l) => l.id)).toEqual([1, 2, 3, 4, 5])
    expect(merged[1].message).toBe("line 2 (replayed)") // later sources win
  })

  it("tolerates missing sources", () => {
    expect(mergeLogLines(undefined, [line(9)])).toEqual([line(9)])
    expect(mergeLogLines()).toEqual([])
  })
})
