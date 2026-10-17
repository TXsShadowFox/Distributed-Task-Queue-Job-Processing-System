import { expect, navigate, signIn, test } from "./fixtures"

test("create a schedule with a live preview, pause it and delete it", async ({ page, account }) => {
  await signIn(page, account)
  await navigate(page, "Schedules")
  await page.getByRole("button", { name: "New schedule" }).click()

  const dialog = page.getByRole("dialog", { name: "New schedule" })
  await dialog.getByLabel("Name").fill("Hourly sync")
  await dialog.getByRole("combobox", { name: "Time zone" }).click()
  // Tokyo: one canonical IANA name in every browser (Kolkata is "Asia/Calcutta" in some ICU builds).
  await page.getByPlaceholder("Search time zones…").fill("Tokyo")
  await page.getByRole("option", { name: "Asia/Tokyo" }).last().click()

  const cron = dialog.getByLabel("Cron expression")
  await cron.fill("61 * * * *")
  await expect(dialog.getByText(/invalid cron expression/)).toBeVisible()

  await dialog.getByRole("button", { name: "Hourly" }).click()
  await expect(cron).toHaveValue("0 * * * *")
  await expect(dialog.getByText("Next runs (Asia/Tokyo)")).toBeVisible()
  await expect(dialog.locator("ol li")).toHaveCount(5)

  await dialog.getByLabel("Task type").click()
  await page.getByRole("option", { name: /demo\.noop/ }).click()
  await dialog.getByRole("button", { name: "Create schedule" }).click()

  const row = page.locator("table tbody tr", { hasText: "Hourly sync" })
  await expect(row).toContainText("Asia/Tokyo")
  await expect(row.locator("[data-status]")).toHaveAttribute("data-status", "ACTIVE")

  await row.getByRole("switch", { name: "Pause Hourly sync" }).click()
  await expect(row.locator("[data-status]")).toHaveAttribute("data-status", "PAUSED")

  await row.getByRole("button", { name: "More actions for Hourly sync" }).click()
  await page.getByRole("menuitem", { name: "Delete" }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete schedule" }).click()
  await expect(page.getByText("No schedules yet")).toBeVisible()
})

test("workers are listed with live capacity; draining needs a platform admin", async ({ page, account }) => {
  await signIn(page, account)
  await navigate(page, "Workers")
  // ":visible": the previous page stays mounted (hidden) for back navigation, and the overview mentions slots too.
  const card = page.locator("section:visible", { hasText: /\d+ of \d+ slots busy/ }).first()
  await expect(card).toBeVisible()
  await expect(card.locator("[data-status]")).toHaveAttribute("data-status", "ACTIVE")
  await expect(page.getByText("only platform administrators can drain or resume them")).toBeVisible()
  await expect(page.getByRole("button", { name: "Drain" })).toHaveCount(0)

  await card.getByRole("button", { name: "Details" }).click()
  await expect(page.getByRole("dialog")).toContainText("Registered task types")
})
