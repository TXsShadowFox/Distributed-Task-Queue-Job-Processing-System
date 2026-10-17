import type { Locator, Page } from "@playwright/test"

import { expect, navigate, signIn, test } from "./fixtures"

const node = (page: Page, key: string) => page.locator(`.react-flow__node[data-id="${key}"]`)

/** Drag from one step's output handle to another step's input handle, as a person would. */
async function connect(page: Page, from: string, to: string) {
  const source = node(page, from).locator(".react-flow__handle.source")
  const target = node(page, to).locator(".react-flow__handle.target")
  await drag(page, source, target)
}

/** A handle's box once it stops moving: adding a step animates the viewport to fit the graph. */
async function settledBox(page: Page, locator: Locator) {
  let previous = await locator.boundingBox()
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(100)
    const current = await locator.boundingBox()
    if (current && previous && current.x === previous.x && current.y === previous.y) return current
    previous = current
  }
  return previous
}

async function drag(page: Page, from: Locator, to: Locator) {
  const a = await settledBox(page, from)
  const b = await settledBox(page, to)
  if (!a || !b) throw new Error("handle not visible")
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.up()
}

test("build a workflow, refuse a cycle, save it and run it", async ({ page, account }) => {
  await signIn(page, account)
  await navigate(page, "Workflows")
  await page.getByRole("link", { name: "New workflow" }).click()
  await page.waitForURL("**/workflows/new")

  await page.getByRole("textbox", { name: "Workflow name" }).fill("E2E pipeline")
  const palette = page.locator("#task-palette")
  await palette.getByRole("button", { name: /^demo\.noop/ }).click()
  await palette.getByRole("button", { name: /^send_email/ }).click()
  await expect(page.locator(".react-flow__node")).toHaveCount(2)
  await expect(node(page, "noop")).toBeVisible()
  await expect(node(page, "send_email")).toBeVisible()

  await connect(page, "noop", "send_email")
  await expect(page.locator(".react-flow__edge")).toHaveCount(1)

  // The reverse edge would close a loop: refused, with the reason.
  await connect(page, "send_email", "noop")
  await expect(page.locator("[data-sonner-toast]", { hasText: "That connection would create a cycle" })).toBeVisible()
  await expect(page.locator(".react-flow__edge")).toHaveCount(1)

  // Editing a step in the inspector.
  await node(page, "send_email").click()
  await page.getByLabel("Name", { exact: true }).fill("Notify ops")
  await expect(node(page, "send_email")).toContainText("Notify ops")

  await page.getByRole("button", { name: "Validate", exact: true }).click()
  await expect(page.locator("[data-sonner-toast]", { hasText: "The workflow is valid" })).toBeVisible()

  await page.getByRole("button", { name: "Create workflow" }).click()
  await page.waitForURL(/\/workflows\/[0-9a-f-]{36}$/)
  await expect(page.getByText("Saved · v1")).toBeVisible()

  await page.getByRole("button", { name: "Run", exact: true }).click()
  await page.getByRole("dialog").getByRole("button", { name: "Start run" }).click()
  await page.waitForURL(/\/workflows\/runs\/[0-9a-f-]{36}$/)
  await expect(page.locator("header [data-status]")).toHaveAttribute("data-status", "SUCCEEDED", { timeout: 30_000 })
  await expect(page.getByText("2 of 2 done")).toBeVisible()
})

test("saving a new version keeps the old one browsable", async ({ page, account, api }) => {
  const wf = await api.post<{ id: string }>("/workflows", {
    name: "Versioned",
    graph: { nodes: [{ key: "only", name: "Only", task_type: "demo.noop" }], edges: [] },
  })
  await signIn(page, account)
  await navigate(page, "Workflows")
  await page.getByRole("link", { name: "Versioned" }).click()
  await page.waitForURL(`**/workflows/${wf.id}`)

  await page.locator("#task-palette").or(page.getByRole("button", { name: "Add step" })).first().waitFor()
  if (await page.getByRole("button", { name: "Add step" }).isVisible()) await page.getByRole("button", { name: "Add step" }).click()
  await page.locator("#task-palette").getByRole("button", { name: /^demo\.noop/ }).click()
  await connect(page, "only", "noop")
  await page.getByRole("button", { name: "Save version" }).click()
  await expect(page.getByText("Saved · v2")).toBeVisible()

  // Deselect the new step so the inspector shows the workflow overview with its versions.
  await page.locator(".react-flow__pane").click({ position: { x: 10, y: 10 } })
  await page.getByRole("link", { name: "v1", exact: true }).click()
  await page.waitForURL(`**/workflows/${wf.id}?version=1`)
  await expect(page.getByText("read-only")).toBeVisible()
  await expect(page.locator(".react-flow__node")).toHaveCount(1)
  await expect(page.getByRole("button", { name: "Restore v1" })).toBeVisible()
})
