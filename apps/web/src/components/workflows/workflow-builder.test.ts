import { describe, expect, it } from "vitest"

import type { StepData, StepNodeType } from "./graph"
import { stepProblems, toGraphIn } from "./workflow-builder"

const KNOWN = new Set(["etl.extract", "etl.load"])

function step(overrides: Partial<StepData> = {}): StepData {
  return {
    name: "Extract",
    taskType: "etl.extract",
    payloadText: '{"source": "orders"}',
    queue: "",
    priority: "",
    maxRetries: "",
    timeoutSeconds: "",
    ...overrides,
  }
}

describe("stepProblems", () => {
  it("accepts a complete step with defaults left blank", () => {
    expect(stepProblems("extract", step(), KNOWN)).toEqual([])
  })

  it("explains every invalid field", () => {
    const problems = stepProblems(
      "1bad key",
      step({
        name: " ",
        taskType: "etl.unknown",
        payloadText: "[1]",
        queue: "Bad Queue",
        priority: "10",
        maxRetries: "-1",
        timeoutSeconds: "1.5",
      }),
      KNOWN,
    )
    expect(problems).toHaveLength(8)
    expect(problems.join("\n")).toMatch(/Key must start with a letter/)
    expect(problems.join("\n")).toMatch(/Unknown task type “etl.unknown”/)
    expect(problems.join("\n")).toMatch(/Payload must be a JSON object/)
    expect(problems.join("\n")).toMatch(/Priority must be a whole number from 0 to 9/)
  })

  it("requires a task type and bounds the name", () => {
    expect(stepProblems("a", step({ taskType: "" }), KNOWN)).toEqual(["Choose a task type."])
    expect(stepProblems("a", step({ name: "x".repeat(121) }), KNOWN)).toEqual(["Name must be at most 120 characters."])
  })
})

describe("toGraphIn", () => {
  it("serializes steps and edges for the API", () => {
    const nodes: StepNodeType[] = [
      { id: "extract", type: "step", position: { x: 10.4, y: -20.6 }, data: step({ priority: "7", queue: " etl " }) },
      { id: "load", type: "step", position: { x: 250, y: 0 }, data: step({ name: " Load ", taskType: "etl.load", payloadText: "" }) },
    ]
    const graph = toGraphIn(nodes, [{ id: "extract->load", source: "extract", target: "load" }])
    expect(graph).toEqual({
      nodes: [
        {
          key: "extract",
          name: "Extract",
          task_type: "etl.extract",
          payload: { source: "orders" },
          queue: "etl",
          priority: 7,
          max_retries: null,
          timeout_seconds: null,
          position: { x: 10, y: -21 },
        },
        {
          key: "load",
          name: "Load",
          task_type: "etl.load",
          payload: {},
          queue: null,
          priority: null,
          max_retries: null,
          timeout_seconds: null,
          position: { x: 250, y: 0 },
        },
      ],
      edges: [{ source: "extract", target: "load" }],
    })
  })
})
