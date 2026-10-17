/**
 * Captures the README screenshots from a running stack with demo data (`python scripts/seed_demo.py`):
 *   npm run screenshots
 * Images are written to docs/screenshots/. They show whatever the stack really contains: nothing is mocked.
 */
import { fileURLToPath } from "node:url"

import { API_URL, expect, navigate, test, type Account } from "./fixtures"

const OUT = fileURLToPath(new URL("../../../docs/screenshots/", import.meta.url))
const demo: Account = {
  email: process.env.FLOWFORGE_DEMO_EMAIL ?? "admin@example.com",
  password: process.env.FLOWFORGE_DEMO_PASSWORD ?? "flowforge-demo-password",
  fullName: "Demo User",
  token: "",
  orgId: "",
  projectId: "",
}

test("README screenshots", async ({ page, request }) => {
  test.setTimeout(180_000)
  // Ids for the detail pages come from the API, as the demo account.
  const login = await request.post(`${API_URL}/api/v1/auth/login`, { data: { email: demo.email, password: demo.password } })
  expect(login.ok(), "demo account (run scripts/seed_demo.py)").toBe(true)
  const token = ((await login.json()) as { access_token: string }).access_token
  const me = await (await request.get(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } })).json()
  const headers = { Authorization: `Bearer ${token}`, "X-Project-ID": me.memberships[0].projects[0].id as string }
  const get = async (path: string) => (await request.get(`${API_URL}/api/v1${path}`, { headers })).json()
  const workflow = ((await get("/workflows")) as { id: string; name: string }[])[0]
  const run = ((await get("/workflow-runs")) as { items: { id: string }[] }).items[0]
  const task = ((await get("/tasks?state=DEAD_LETTERED&limit=1")) as { items: { id: string }[] }).items[0]

  await page.goto("/login")
  await page.getByLabel("Email").fill(demo.email)
  await page.getByLabel("Password").fill(demo.password)
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL("**/overview")
  // The dev server's floating indicator is not part of the product.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" })

  const shot = async (name: string) => {
    // Not "networkidle": the live event stream keeps a request open for as long as the page is.
    await expect(page.locator(".animate-pulse:visible")).toHaveCount(0) // loading skeletons gone
    await page.waitForTimeout(800) // let charts finish their entry animation
    await page.screenshot({ path: `${OUT}${name}.png` })
  }

  await shot("overview")
  await navigate(page, "Tasks")
  await shot("tasks")
  if (task) {
    const search = page.getByRole("textbox", { name: "Search tasks", exact: true })
    await search.fill(task.id)
    await search.press("Enter")
    await page.getByRole("link", { name: new RegExp(task.id.slice(-4)) }).first().click()
    await page.waitForURL(`**/tasks/${task.id}`)
    await shot("task-detail")
  }
  await navigate(page, "Workflows")
  await shot("workflows")
  // Detail pages are opened through their links: in-app navigation keeps the in-memory session.
  if (run) {
    await page.getByRole("link", { name: new RegExp(run.id.slice(-4)) }).first().click()
    await page.waitForURL(`**/workflows/runs/${run.id}`)
    await expect(page.locator(".react-flow__node").first()).toBeVisible()
    await shot("workflow-run")
    await navigate(page, "Workflows")
  }
  if (workflow) {
    await page.getByRole("link", { name: workflow.name, exact: true }).first().click()
    await page.waitForURL(`**/workflows/${workflow.id}`)
    await expect(page.locator(".react-flow__node").first()).toBeVisible()
    await shot("workflow-builder")
  }
  await navigate(page, "Workers")
  await shot("workers")
  await navigate(page, "Schedules")
  await shot("schedules")
  await navigate(page, "Analytics")
  await shot("analytics")
  await navigate(page, "Dead Letter Queue")
  await shot("dead-letter")
  await navigate(page, "Settings")
  await shot("settings")
})
