import { expect, navigate, signIn, test } from "./fixtures"

test("submit a task from the dashboard and watch it succeed live", async ({ page, account }) => {
  await signIn(page, account)
  await page.getByRole("button", { name: "Submit task" }).click()

  const dialog = page.getByRole("dialog", { name: "Submit a task" })
  await dialog.getByLabel("Task type").click()
  await page.getByRole("option", { name: /send_email/ }).click()
  await expect(dialog.getByLabel("Payload (JSON)")).toHaveValue(/"to"/) // the type's example payload
  await dialog.getByRole("button", { name: "Submit", exact: true }).click()

  // The toast links straight to the task.
  await page.locator("[data-sonner-toast]").getByRole("button", { name: "Open", exact: true }).click()
  await page.waitForURL(/\/tasks\/[0-9a-f-]{36}$/)
  await expect(page.getByRole("heading", { name: "send_email" })).toBeVisible()

  // The status updates without a reload (SSE + query invalidation).
  await expect(page.locator("header [data-status]")).toHaveAttribute("data-status", "SUCCEEDED", { timeout: 30_000 })

  await page.getByRole("tab", { name: "Timeline" }).click()
  await expect(page.getByText("Queued for execution")).toBeVisible()
  await page.getByRole("tab", { name: /Attempts/ }).click()
  await expect(page.getByRole("cell", { name: "#1" })).toBeVisible()
  await page.getByRole("tab", { name: "Logs" }).click()
  await expect(page.getByRole("log", { name: "Task log" })).toContainText("attempt started")
  await page.getByRole("tab", { name: "Payload & result" }).click()
  await expect(page.getByText('"to"').first()).toBeVisible()
})

test("filters, search and sorting live in the URL", async ({ page, account, api }) => {
  const ok = await api.submit({ task_type: "demo.noop", payload: { tag: "first" } })
  const second = await api.submit({ task_type: "demo.noop", payload: { tag: "second" } })
  const bad = await api.submit({ task_type: "demo.fail", payload: { message: "nope" }, max_retries: 0 })
  await api.waitForTask(ok.id, ["SUCCEEDED"])
  await api.waitForTask(second.id, ["SUCCEEDED"])
  await api.waitForTask(bad.id, ["DEAD_LETTERED"])

  await signIn(page, account)
  await navigate(page, "Tasks")
  const rows = page.locator("table tbody tr")
  await expect(rows).toHaveCount(3)
  await expect(page.getByText("3 of 3 matching tasks")).toBeVisible()

  await page.getByRole("button", { name: "State" }).click()
  // The checked state follows the URL, so it updates a render after the click (check() would not wait).
  const deadLettered = page.getByRole("checkbox", { name: "Dead Lettered" })
  await deadLettered.click()
  await expect(deadLettered).toBeChecked()
  await page.keyboard.press("Escape")
  await expect(page).toHaveURL(/state=DEAD_LETTERED/)
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText("demo.fail")

  // A shared link reproduces the same view.
  await page.reload()
  await expect(page.getByRole("button", { name: "State · 1" })).toBeVisible()
  await expect(rows).toHaveCount(1)

  await page.getByRole("button", { name: "Clear" }).click()
  await expect(rows).toHaveCount(3)

  const search = page.getByRole("textbox", { name: "Search tasks", exact: true })
  await search.fill(second.id)
  await search.press("Enter")
  await expect(page).toHaveURL(new RegExp(`q=${second.id}`))
  await expect(rows).toHaveCount(1)
  await rows.first().click()
  await page.waitForURL(`**/tasks/${second.id}`)
  await expect(page.getByText(second.id)).toBeVisible()
})

test("cancel a delayed task", async ({ page, account, api }) => {
  const delayed = await api.submit({ task_type: "demo.noop", delay_seconds: 3600 })
  expect(delayed.state).toBe("SCHEDULED")

  await signIn(page, account)
  await navigate(page, "Tasks")
  await page.getByRole("link", { name: new RegExp(delayed.id.slice(-4)) }).click()
  await page.getByRole("button", { name: "Cancel task" }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel task" }).click()
  await expect(page.locator("header [data-status]")).toHaveAttribute("data-status", "CANCELLED")
  // Cancelled tasks can be retried.
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible()
})
