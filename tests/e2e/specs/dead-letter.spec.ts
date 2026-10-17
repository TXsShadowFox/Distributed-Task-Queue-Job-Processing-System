import { expect, navigate, signIn, test } from "./fixtures"

test("a failing task retries with backoff, is dead-lettered, then retried from the queue", async ({ page, account, api }) => {
  const task = await api.submit({
    task_type: "demo.fail",
    payload: { message: "card declined", retryable: true },
    max_retries: 1,
    retry_backoff_seconds: 1,
  })
  const dead = await api.waitForTask(task.id, ["DEAD_LETTERED"])
  expect(dead.attempt_count).toBe(2) // the first attempt plus one retry

  await signIn(page, account)
  await navigate(page, "Dead Letter Queue")
  const row = page.locator("table tbody tr", { hasText: "demo.fail" })
  await expect(row).toHaveCount(1)
  await expect(row).toContainText("RetryableError")

  await row.getByRole("button", { name: "Inspect" }).click()
  const sheet = page.getByRole("dialog")
  await expect(sheet).toContainText("card declined")
  await expect(sheet).toContainText("1 of 1") // retries used
  await expect(sheet.getByText("Last traceback")).toBeVisible()
  await sheet.getByRole("button", { name: "Retry" }).click()
  await expect(page.locator("[data-sonner-toast]")).toContainText("Task re-queued")

  // Leaves the queue, gets a fresh retry budget and runs (and fails) again.
  await expect(page.locator("table tbody tr", { hasText: "demo.fail" })).toHaveCount(0, { timeout: 15_000 })
  const again = await api.waitForTask(task.id, ["DEAD_LETTERED"])
  expect(again.attempt_count).toBe(4)
})

test("bulk archive hides tasks until archived ones are shown", async ({ page, account, api }) => {
  const ids = []
  for (const message of ["first", "second"]) {
    const t = await api.submit({ task_type: "demo.fail", payload: { message }, max_retries: 0 })
    ids.push(t.id)
  }
  for (const id of ids) await api.waitForTask(id, ["DEAD_LETTERED"])

  await signIn(page, account)
  await navigate(page, "Dead Letter Queue")
  const rows = page.locator("table tbody tr")
  await expect(rows).toHaveCount(2)

  await page.getByRole("checkbox", { name: "Select every task on this page" }).click()
  await expect(page.getByText("2 selected")).toBeVisible()
  await page.getByRole("toolbar", { name: "Bulk actions" }).getByRole("button", { name: "Archive" }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Archive" }).click()
  await expect(page.locator("[data-sonner-toast]")).toContainText("2 tasks archived")
  await expect(page.getByText("The dead letter queue is empty")).toBeVisible()

  await page.getByRole("switch", { name: "Include archived" }).click()
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toContainText("archived")
})
