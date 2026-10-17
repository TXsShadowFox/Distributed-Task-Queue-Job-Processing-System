// Visual check helper: signs in, visits routes, saves screenshots and prints console errors.
//   node tools/peek.mjs <outDir> <route> [route...]
// Env: FLOWFORGE_WEB_URL, FLOWFORGE_EMAIL, FLOWFORGE_PASSWORD, PW_CHANNEL, PEEK_THEME=dark|light, PEEK_WIDTH
import { chromium } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

const [outDir = "peek", ...routes] = process.argv.slice(2)
const base = process.env.FLOWFORGE_WEB_URL ?? "http://localhost:3000"
const email = process.env.FLOWFORGE_EMAIL ?? "admin@example.com"
const password = process.env.FLOWFORGE_PASSWORD ?? "flowforge-demo-password"
const width = Number(process.env.PEEK_WIDTH ?? 1440)
mkdirSync(outDir, { recursive: true })

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined) })
const context = await browser.newContext({ viewport: { width, height: 900 } })
if (process.env.PEEK_THEME) {
  await context.addInitScript((theme) => localStorage.setItem("ff-theme", theme), process.env.PEEK_THEME)
}
const page = await context.newPage()
const problems = []
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "warning") problems.push(`[${m.type()}] ${m.text()}`)
})
page.on("pageerror", (e) => problems.push(`[pageerror] ${e.message}`))

await page.goto(`${base}/login`)
await page.getByLabel("Email").fill(email)
await page.getByLabel("Password").fill(password)
await page.getByRole("button", { name: "Sign in" }).click()
await page.waitForURL(/\/overview/, { timeout: 30_000 })

for (const route of routes.length ? routes : ["/overview"]) {
  await page.goto(`${base}${route}`)
  await page.waitForLoadState("networkidle").catch(() => {})
  await page.waitForTimeout(1500)
  const file = join(outDir, `${route.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "root"}.png`)
  await page.screenshot({ path: file, fullPage: true })
  console.log(`saved ${file}`)
}
console.log(problems.length ? `\n${problems.length} console problems:\n${problems.join("\n")}` : "\nno console errors or warnings")
await browser.close()
