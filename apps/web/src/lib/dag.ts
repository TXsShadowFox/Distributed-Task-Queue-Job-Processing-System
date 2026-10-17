/** Client-side DAG helpers for the workflow builder. The server re-validates every graph on save. */

export interface Edge {
  source: string
  target: string
}

export function adjacency(edges: Edge[]): Map<string, string[]> {
  const succ = new Map<string, string[]>()
  for (const { source, target } of edges) {
    const list = succ.get(source) ?? []
    list.push(target)
    succ.set(source, list)
  }
  return succ
}

/** True if `to` is reachable from `from`. */
export function reachable(edges: Edge[], from: string, to: string): boolean {
  const succ = adjacency(edges)
  const stack = [from]
  const seen = new Set<string>()
  while (stack.length) {
    const node = stack.pop() as string
    if (node === to) return true
    if (seen.has(node)) continue
    seen.add(node)
    stack.push(...(succ.get(node) ?? []))
  }
  return false
}

/** A shortest path from `from` to `to` (both included), or null when `to` is unreachable. */
export function findPath(edges: Edge[], from: string, to: string): string[] | null {
  const succ = adjacency(edges)
  const prev = new Map<string, string | null>([[from, null]])
  const queue = [from]
  while (queue.length) {
    const node = queue.shift() as string
    if (node === to) {
      const path: string[] = []
      for (let cur: string | null = to; cur !== null; cur = prev.get(cur) ?? null) path.unshift(cur)
      return path
    }
    for (const next of succ.get(node) ?? []) {
      if (!prev.has(next)) {
        prev.set(next, node)
        queue.push(next)
      }
    }
  }
  return null
}

/** Adding source → target creates a cycle iff source is reachable from target (or it is a self-loop). */
export function wouldCreateCycle(edges: Edge[], source: string, target: string): boolean {
  return source === target || reachable(edges, target, source)
}

/** Kahn's algorithm; returns null when the graph has a cycle. */
export function topologicalOrder(nodes: string[], edges: Edge[]): string[] | null {
  const indegree = new Map(nodes.map((n) => [n, 0]))
  for (const { target } of edges) indegree.set(target, (indegree.get(target) ?? 0) + 1)
  const succ = adjacency(edges)
  const queue = nodes.filter((n) => indegree.get(n) === 0)
  const order: string[] = []
  while (queue.length) {
    const node = queue.shift() as string
    order.push(node)
    for (const next of succ.get(node) ?? []) {
      const d = (indegree.get(next) ?? 0) - 1
      indegree.set(next, d)
      if (d === 0) queue.push(next)
    }
  }
  return order.length === nodes.length ? order : null
}

/**
 * Stages of nodes that can run in parallel: a node's stage is the longest path to it from a root, so it starts only
 * after everything in earlier stages that it depends on. Null when the graph has a cycle.
 */
export function stages(nodes: string[], edges: Edge[]): string[][] | null {
  const order = topologicalOrder(nodes, edges)
  if (!order) return null
  const preds = new Map<string, string[]>()
  for (const { source, target } of edges) preds.set(target, [...(preds.get(target) ?? []), source])
  const depth = new Map<string, number>()
  const out: string[][] = []
  for (const node of order) {
    const d = Math.max(0, ...(preds.get(node) ?? []).map((p) => (depth.get(p) ?? 0) + 1))
    depth.set(node, d)
    ;(out[d] ??= []).push(node)
  }
  return out
}

/** Layered layout: depth = longest path from a root; nodes in a layer are spread vertically. */
export function layeredLayout(
  nodes: string[],
  edges: Edge[],
  { xGap = 240, yGap = 120 }: { xGap?: number; yGap?: number } = {},
): Record<string, { x: number; y: number }> {
  const order = topologicalOrder(nodes, edges) ?? nodes
  const depth = new Map<string, number>(nodes.map((n) => [n, 0]))
  const preds = new Map<string, string[]>()
  for (const { source, target } of edges) preds.set(target, [...(preds.get(target) ?? []), source])
  for (const node of order) {
    const d = Math.max(0, ...(preds.get(node) ?? []).map((p) => (depth.get(p) ?? 0) + 1))
    depth.set(node, d)
  }
  const layers = new Map<number, string[]>()
  for (const node of order) {
    const d = depth.get(node) ?? 0
    layers.set(d, [...(layers.get(d) ?? []), node])
  }
  const positions: Record<string, { x: number; y: number }> = {}
  for (const [d, layer] of layers) {
    layer.forEach((node, i) => {
      positions[node] = { x: d * xGap, y: (i - (layer.length - 1) / 2) * yGap }
    })
  }
  return positions
}

const KEY_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/

export function isValidNodeKey(key: string): boolean {
  return KEY_RE.test(key)
}

export function uniqueKey(base: string, taken: Set<string>): string {
  const cleaned = base.replace(/[^A-Za-z0-9_-]/g, "_").replace(/^[^A-Za-z]+/, "") || "step"
  let candidate = cleaned.slice(0, 60)
  let i = 2
  while (taken.has(candidate)) candidate = `${cleaned.slice(0, 56)}_${i++}`
  return candidate
}
