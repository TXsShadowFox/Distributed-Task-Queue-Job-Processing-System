/**
 * Shared E2E fixtures. Every test registers its own account (a fresh organization and project), so tests never see
 * each other's data and need no seed. Setup that is not under test goes through the API; the UI is exercised for
 * what each spec is about.
 *
 * Page loads restore the session through /auth/refresh, which shares the per-IP auth rate limit with login and
 * register. Specs therefore navigate in-app (like a person would) and the stack used for E2E runs should raise
 * FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE (see tests/e2e/README.md).
 */
import { test as base, expect, type APIRequestContext, type APIResponse, type Page } from "@playwright/test"

export const API_URL = (process.env.FLOWFORGE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "")
export const PASSWORD = "e2e-correct-horse-battery"

export interface Account {
  email: string
  password: string
  fullName: string
  token: string
  orgId: string
  projectId: string
}

export interface TaskBody {
  task_type: string
  payload?: Record<string, unknown>
  max_retries?: number
  retry_backoff_seconds?: number
  delay_seconds?: number
  priority?: number
}

export interface TaskRow {
  id: string
  state: string
  task_type: string
  attempt_count: number
  retry_count: number
}

const uniqueEmail = (tag: string) => `${tag}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}@example.com`

async function withRateLimitRetry(call: () => Promise<APIResponse>): Promise<APIResponse> {
  for (let attempt = 0; ; attempt++) {
    const response = await call()
    if (response.status() !== 429 || attempt >= 3) return response
    const wait = Number(response.headers()["retry-after"] ?? "5")
    await new Promise((resolve) => setTimeout(resolve, Math.min(60, wait) * 1000))
  }
}

export async function registerAccount(request: APIRequestContext, tag = "e2e"): Promise<Account> {
  const email = uniqueEmail(tag)
  const fullName = "E2E Tester"
  const response = await withRateLimitRetry(() =>
    request.post(`${API_URL}/api/v1/auth/register`, {
      data: { email, password: PASSWORD, full_name: fullName, organization_name: `E2E ${tag}` },
    }),
  )
  expect(response.status(), "registration").toBe(201)
  const { access_token: token } = (await response.json()) as { access_token: string }
  const me = await request.get(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
  const body = (await me.json()) as { memberships: { organization_id: string; projects: { id: string }[] }[] }
  return {
    email,
    password: PASSWORD,
    fullName,
    token,
    orgId: body.memberships[0].organization_id,
    projectId: body.memberships[0].projects[0].id,
  }
}

/** Thin API client acting as the account, for setup and for asserting server-side state. */
export class Api {
  constructor(
    private readonly request: APIRequestContext,
    private readonly account: Account,
  ) {}

  private headers() {
    return { Authorization: `Bearer ${this.account.token}`, "X-Project-ID": this.account.projectId }
  }

  async get<T>(path: string): Promise<T> {
    const response = await this.request.get(`${API_URL}/api/v1${path}`, { headers: this.headers() })
    expect(response.ok(), `GET ${path}: ${await response.text()}`).toBe(true)
    return (await response.json()) as T
  }

  async post<T>(path: string, data?: unknown): Promise<T> {
    const response = await this.request.post(`${API_URL}/api/v1${path}`, { headers: this.headers(), data })
    expect(response.ok(), `POST ${path}: ${await response.text()}`).toBe(true)
    return (await response.json()) as T
  }

  submit(body: TaskBody): Promise<TaskRow> {
    return this.post<TaskRow>("/tasks", { payload: {}, ...body })
  }

  async waitForTask(id: string, states: string[], timeoutMs = 45_000): Promise<TaskRow> {
    let task = await this.get<TaskRow>(`/tasks/${id}`)
    await expect
      .poll(
        async () => {
          task = await this.get<TaskRow>(`/tasks/${id}`)
          return task.state
        },
        { timeout: timeoutMs, intervals: [250, 500, 1000] },
      )
      .toMatch(new RegExp(`^(${states.join("|")})$`))
    return task
  }
}

export async function signIn(page: Page, account: Account, path = "/overview") {
  await page.goto(path === "/overview" ? "/login" : `/login?next=${encodeURIComponent(path)}`)
  await page.getByLabel("Email").fill(account.email)
  await page.getByLabel("Password").fill(account.password)
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL((url) => url.pathname === path.split("?")[0], { timeout: 30_000 })
}

/** In-app navigation through the sidebar keeps the in-memory session (no refresh round trip). */
export async function navigate(page: Page, label: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label, exact: true }).click()
}

export const test = base.extend<{ account: Account; api: Api }>({
  account: async ({ request }, use) => {
    await use(await registerAccount(request))
  },
  api: async ({ request, account }, use) => {
    await use(new Api(request, account))
  },
})

export { expect }
