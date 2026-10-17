import { describe, expect, it } from "vitest"

import {
  findPath,
  isValidNodeKey,
  layeredLayout,
  reachable,
  stages,
  topologicalOrder,
  uniqueKey,
  wouldCreateCycle,
  type Edge,
} from "./dag"

const e = (source: string, target: string): Edge => ({ source, target })
// extract_a ─┐
//            ├─> transform ─> load
// extract_b ─┘
const diamond = [e("extract_a", "transform"), e("extract_b", "transform"), e("transform", "load")]
const nodes = ["extract_a", "extract_b", "transform", "load"]

describe("reachability and cycles", () => {
  it("follows edges transitively", () => {
    expect(reachable(diamond, "extract_a", "load")).toBe(true)
    expect(reachable(diamond, "load", "extract_a")).toBe(false)
  })

  it("rejects self-loops and back edges, allows forward and parallel edges", () => {
    expect(wouldCreateCycle(diamond, "load", "load")).toBe(true)
    expect(wouldCreateCycle(diamond, "load", "extract_a")).toBe(true)
    expect(wouldCreateCycle(diamond, "transform", "extract_b")).toBe(true)
    expect(wouldCreateCycle(diamond, "extract_a", "load")).toBe(false)
    expect(wouldCreateCycle(diamond, "extract_a", "extract_b")).toBe(false)
  })

  it("finds the shortest path that explains a cycle", () => {
    expect(findPath(diamond, "extract_a", "load")).toEqual(["extract_a", "transform", "load"])
    expect(findPath(diamond, "load", "extract_a")).toBeNull()
    expect(findPath(diamond, "transform", "transform")).toEqual(["transform"])
  })
})

describe("ordering", () => {
  it("orders topologically and returns null for cycles", () => {
    const order = topologicalOrder(nodes, diamond)
    expect(order).not.toBeNull()
    expect(order!.indexOf("transform")).toBeGreaterThan(order!.indexOf("extract_a"))
    expect(order!.indexOf("load")).toBe(3)
    expect(topologicalOrder(["a", "b"], [e("a", "b"), e("b", "a")])).toBeNull()
  })

  it("groups parallel steps into stages by longest path", () => {
    expect(stages(nodes, diamond)).toEqual([["extract_a", "extract_b"], ["transform"], ["load"]])
    // A shortcut edge does not pull a node forward: c still waits for b.
    expect(stages(["a", "b", "c"], [e("a", "b"), e("b", "c"), e("a", "c")])).toEqual([["a"], ["b"], ["c"]])
    expect(stages(["a", "b"], [e("a", "b"), e("b", "a")])).toBeNull()
  })

  it("lays stages out left to right and centres each column", () => {
    const pos = layeredLayout(nodes, diamond, { xGap: 100, yGap: 50 })
    expect(pos.transform).toEqual({ x: 100, y: 0 })
    expect(pos.load).toEqual({ x: 200, y: 0 })
    expect([pos.extract_a.y, pos.extract_b.y].sort((a, b) => a - b)).toEqual([-25, 25])
    expect(pos.extract_a.x).toBe(0)
  })
})

describe("node keys", () => {
  it.each([
    ["extract", true],
    ["load_2", true],
    ["a-b", true],
    ["2fast", false],
    ["has space", false],
    ["", false],
    ["x".repeat(65), false],
  ])("isValidNodeKey(%j) is %s", (key, valid) => {
    expect(isValidNodeKey(key)).toBe(valid)
  })

  it("derives unique, valid keys", () => {
    const taken = new Set(["extract", "extract_2"])
    expect(uniqueKey("extract", taken)).toBe("extract_3")
    expect(uniqueKey("demo.cpu hash", new Set())).toBe("demo_cpu_hash")
    expect(uniqueKey("42", new Set())).toBe("step")
    expect(isValidNodeKey(uniqueKey("x".repeat(200), new Set()))).toBe(true)
  })
})
