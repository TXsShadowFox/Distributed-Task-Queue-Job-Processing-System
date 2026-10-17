import { describe, expect, it } from "vitest"

import { parseJsonObject, prettyJson } from "./json"

describe("parseJsonObject", () => {
  it("treats blank input as an empty object", () => {
    expect(parseJsonObject("   ")).toEqual({ value: {} })
  })

  it("accepts objects only", () => {
    expect(parseJsonObject('{"to": "ada@example.com"}')).toEqual({ value: { to: "ada@example.com" } })
    expect(parseJsonObject("[1, 2]", "Payload")).toEqual({ error: "Payload must be a JSON object" })
    expect(parseJsonObject("null", "Run input")).toEqual({ error: "Run input must be a JSON object" })
    expect(parseJsonObject("42").error).toBe("Value must be a JSON object")
  })

  it("reports syntax errors", () => {
    expect(parseJsonObject("{oops}").error).toMatch(/^Invalid JSON: /)
  })

  it("pretty-prints with two-space indentation and treats nullish as {}", () => {
    expect(prettyJson({ a: 1 })).toBe('{\n  "a": 1\n}')
    expect(prettyJson(undefined)).toBe("{}")
  })
})
