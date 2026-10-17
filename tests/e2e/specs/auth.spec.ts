import { expect, PASSWORD, test } from "./fixtures"

test("register, sign out, get bounced from a protected page and sign back in", async ({ page }) => {
  const email = `e2e-register-${Date.now().toString(36)}@example.com`
  await page.goto("/register")
  await page.getByLabel("Full name").fill("Grace Hopper")
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Organization (optional)").fill("Compilers Inc.")
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD)
  await page.getByLabel("Confirm password").fill(PASSWORD)
  await page.getByRole("button", { name: "Create account" }).click()

  await page.waitForURL("**/overview")
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible()

  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("menuitem", { name: "Sign out" }).click()
  await page.waitForURL("**/login**")

  // Protected pages redirect to sign-in and remember where you were going.
  await page.goto("/schedules")
  await page.waitForURL(/\/login\?next=%2Fschedules/)

  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password").fill("not-the-password")
  await page.getByRole("button", { name: "Sign in" }).click()
  // Scoped to the form: Next.js also renders a route announcer with role="alert".
  await expect(page.locator("form").getByRole("alert")).toContainText("invalid email or password")

  await page.getByLabel("Password").fill(PASSWORD)
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL("**/schedules")
  await expect(page.getByRole("heading", { name: "Schedules" })).toBeVisible()
})

test("registration validates input before calling the API", async ({ page }) => {
  await page.goto("/register")
  await page.getByLabel("Full name").fill("Short Password")
  await page.getByLabel("Email").fill("not-an-email")
  await page.getByLabel("Password", { exact: true }).fill("short")
  await page.getByLabel("Confirm password").fill("different")
  await page.getByRole("button", { name: "Create account" }).click()
  await expect(page.locator("#email-error")).not.toBeEmpty()
  await expect(page.locator("#password-error")).not.toBeEmpty()
  await expect(page).toHaveURL(/\/register/)
})
