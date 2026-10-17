import { expect, navigate, signIn, test } from "./fixtures"

const PAGES: [label: string, heading: string][] = [
  ["Tasks", "Tasks"],
  ["Workflows", "Workflows"],
  ["Workers", "Workers"],
  ["Schedules", "Schedules"],
  ["Analytics", "Analytics"],
  ["Dead Letter Queue", "Dead letter queue"],
  ["API Keys", "API keys"],
  ["Settings", "Settings"],
]

test("every page works on a phone without sideways scrolling", async ({ page, account, api }) => {
  await api.submit({ task_type: "demo.noop" })
  await signIn(page, account)
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible()

  for (const [label, heading] of PAGES) {
    await page.getByRole("button", { name: "Open navigation" }).click()
    await navigate(page, label)
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible()
    // Wide content (tables, charts) scrolls inside its card; the page itself never does.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow, `${label} overflows horizontally by ${overflow}px`).toBeLessThanOrEqual(1)
  }
})

test("submitting a task works from a phone", async ({ page, account }) => {
  await signIn(page, account)
  await page.getByRole("button", { name: "Submit task" }).click()
  const dialog = page.getByRole("dialog", { name: "Submit a task" })
  await dialog.getByLabel("Task type").click()
  await page.getByRole("option", { name: /demo\.noop/ }).click()
  await dialog.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(page.locator("[data-sonner-toast]")).toContainText("demo.noop queued")
})
