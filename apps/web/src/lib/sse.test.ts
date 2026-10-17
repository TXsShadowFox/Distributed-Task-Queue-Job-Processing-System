import { describe, expect, it } from "vitest"

import { backoffDelay, parseFrames } from "./sse"

describe("parseFrames", () => {
  it("splits complete frames and keeps the incomplete tail", () => {
    const { frames, rest } = parseFrames('data: {"a":1}\n\ndata: {"b":2}\n\ndata: {"c"')
    expect(frames).toEqual([
      { event: null, data: '{"a":1}' },
      { event: null, data: '{"b":2}' },
    ])
    expect(rest).toBe('data: {"c"')
  })

  it("reassembles a frame split across chunks", () => {
    const first = parseFrames('event: control\ndata: {"type":"re')
    expect(first.frames).toEqual([])
    const second = parseFrames(first.rest + 'ady"}\n\n')
    expect(second.frames).toEqual([{ event: "control", data: '{"type":"ready"}' }])
    expect(second.rest).toBe("")
  })

  it("ignores comments and keepalives, joins multi-line data and accepts CRLF", () => {
    const input = ": keepalive\r\n\r\nretry: 3000\n\ndata: line one\r\ndata: line two\r\n\r\n"
    expect(parseFrames(input).frames).toEqual([{ event: null, data: "line one\nline two" }])
  })
})

describe("backoffDelay", () => {
  it("doubles per attempt with jitter and caps at 30 s", () => {
    expect(backoffDelay(1, () => 1)).toBe(2_000)
    expect(backoffDelay(1, () => 0)).toBe(1_000) // jitter keeps at least half
    expect(backoffDelay(3, () => 1)).toBe(8_000)
    expect(backoffDelay(10, () => 1)).toBe(30_000)
    expect(backoffDelay(10, () => 0)).toBe(15_000)
  })
})
