"use client"

import type { TokenOut } from "@/lib/api/types"
import { useSession } from "@/lib/session"

const ORIGIN = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "")
export const API_BASE = `${ORIGIN}/api/v1`

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details: unknown = null,
    readonly requestId: string | null = null,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  body?: unknown
  query?: Query
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Send X-Project-ID (default true). */
  project?: boolean
}

function buildUrl(path: string, query?: Query): string {
  // API_BASE is relative ("/api/v1") when the dashboard and API share an origin behind the reverse proxy.
  const url = new URL(API_BASE + path, typeof window === "undefined" ? "http://localhost" : window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, String(v)))
    else url.searchParams.set(key, String(value))
  }
  return url.toString()
}

async function toError(response: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {}
  try {
    body = await response.json()
  } catch {
    // Non-JSON error (proxy, network appliance): fall back to the status line.
  }
  const err = body.error ?? {}
  return new ApiError(
    err.message ?? `Request failed (${response.status})`,
    response.status,
    err.code ?? "http_error",
    err.details ?? null,
    err.request_id ?? response.headers.get("x-request-id"),
  )
}

// ------------------------------------------------------------------------------------------------ session refresh
let refreshInFlight: Promise<boolean> | null = null

/** Single-flight: concurrent 401s trigger one refresh, and every caller awaits the same result. */
export function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "X-Requested-With": "flowforge" },
      })
      if (!response.ok) return false
      useSession.getState().setToken((await response.json()) as TokenOut)
      return true
    } catch {
      return false
    } finally {
      queueMicrotask(() => {
        refreshInFlight = null
      })
    }
  })()
  return refreshInFlight
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal, project = true } = options
  const send = () => {
    const { accessToken, projectId } = useSession.getState()
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers }
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`
    if (project && projectId) headers["X-Project-ID"] = projectId
    if (body !== undefined) headers["Content-Type"] = "application/json"
    return fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      signal,
    })
  }

  let response = await send()
  if (response.status === 401 && !path.startsWith("/auth/")) {
    if (await refreshSession()) {
      response = await send()
    } else {
      useSession.getState().setAnonymous()
    }
  }
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Auth endpoints that use the httpOnly refresh cookie require the CSRF header. */
export async function authPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "flowforge",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw await toError(response)
  return (await response.json()) as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return "Something went wrong"
}
