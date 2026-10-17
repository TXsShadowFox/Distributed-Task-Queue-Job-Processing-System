import type { APIRequestContext, Page } from "@playwright/test"

import { API_URL, expect, navigate, signIn, test } from "./fixtures"

async function readSecret(page: Page): Promise<string> {
  const secret = (await page.getByTestId("api-key-secret").textContent())?.trim() ?? ""
  expect(secret).toMatch(/^ffk_/)
  // The dialog cannot be dismissed until the user confirms they stored the secret.
  await expect(page.getByRole("button", { name: "Done" })).toBeDisabled()
  await page.getByRole("checkbox", { name: "I have stored the key somewhere safe" }).click()
  await page.getByRole("button", { name: "Done" }).click()
  return secret
}

const listTasks = (request: APIRequestContext, secret: string) =>
  request.get(`${API_URL}/api/v1/tasks`, { headers: { Authorization: `Bearer ${secret}` } })

test("create, use, rotate and revoke an API key", async ({ page, account, request }) => {
  await signIn(page, account)
  await navigate(page, "API Keys")
  await page.getByRole("button", { name: "Create key" }).first().click()

  const dialog = page.getByRole("dialog", { name: "Create an API key" })
  await dialog.getByLabel("Name").fill("ci-e2e")
  await dialog.getByRole("button", { name: "Submit & track tasks" }).click()
  await dialog.getByRole("button", { name: "Create key" }).click()
  const first = await readSecret(page)

  // The key works within its scopes and project only.
  expect((await listTasks(request, first)).status()).toBe(200)
  const submitted = await request.post(`${API_URL}/api/v1/tasks`, {
    headers: { Authorization: `Bearer ${first}` },
    data: { task_type: "demo.noop", payload: {} },
  })
  expect(submitted.status()).toBe(201)
  const manage = await request.get(`${API_URL}/api/v1/api-keys`, { headers: { Authorization: `Bearer ${first}` } })
  expect(manage.status()).toBe(403)

  const row = page.locator("tr[data-key-name='ci-e2e']")
  await expect(row).toContainText("tasks:write")
  await row.getByRole("button", { name: "Rotate" }).click()
  const rotate = page.getByRole("dialog", { name: "Rotate ci-e2e" })
  await rotate.getByLabel("Old secret stops working").click()
  await page.getByRole("option", { name: "Immediately" }).click()
  await rotate.getByRole("button", { name: "Rotate key" }).click()
  const second = await readSecret(page)
  expect(second).not.toBe(first)

  await expect.poll(async () => (await listTasks(request, first)).status()).toBe(401)
  expect((await listTasks(request, second)).status()).toBe(200)

  const current = page.locator("tr[data-key-name='ci-e2e']").filter({ hasText: "Active" })
  await current.getByRole("button", { name: "Revoke" }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Revoke key" }).click()
  await expect(page.locator("[data-sonner-toast]")).toContainText("Revoked ci-e2e")
  expect((await listTasks(request, second)).status()).toBe(401)

  await page.getByRole("switch", { name: /Show revoked/ }).click()
  await expect(page.locator("tr[data-key-name='ci-e2e']").filter({ hasText: "Revoked" }).first()).toBeVisible()
})
