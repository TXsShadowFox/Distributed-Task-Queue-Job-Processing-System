"use client"

import { API_BASE, refreshSession } from "@/lib/api/client"
import { useSession } from "@/lib/session"

/**
 * Server-Sent Events over fetch (not EventSource) so the access token travels in the Authorization header instead of
 * the URL, where proxies and browser history would record it.
 */

export type StreamStatus = "connecting" | "live" | "reconnecting" | "offline"

export interface StreamMessage {
  type?: string
  [key: string]: unknown
}

/** Parse an SSE byte stream into (event, data) frames; `rest` is the incomplete tail to prepend to the next chunk. */
export function parseFrames(buffer: string): { frames: { event: string | null; data: string }[]; rest: string } {
  const frames: { event: string | null; data: string }[] = []
  let rest = buffer.replace(/\r\n/g, "\n")
  let boundary = rest.indexOf("\n\n")
  while (boundary !== -1) {
    const raw = rest.slice(0, boundary)
    rest = rest.slice(boundary + 2)
    let event: string | null = null
    const data: string[] = []
    for (const line of raw.split("\n")) {
      if (line.startsWith(":")) continue // comment / keepalive
      if (line.startsWith("event:")) event = line.slice(6).trim()
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
    }
    if (data.length) frames.push({ event, data: data.join("\n") })
    boundary = rest.indexOf("\n\n")
  }
  return { frames, rest }
}

/** Full-jitter exponential backoff: ~1 s, 2 s, 4 s … capped at 30 s. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  return Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5)) * (0.5 + random() / 2)
}

type StreamEnd = "ended" | "expired" | "unauthorized"

async function readOnce(
  path: string,
  projectId: string,
  signal: AbortSignal,
  onOpen: () => void,
  onMessage: (message: StreamMessage) => void,
): Promise<StreamEnd> {
  const { accessToken } = useSession.getState()
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "text/event-stream",
      Authorization: `Bearer ${accessToken ?? ""}`,
      "X-Project-ID": projectId,
    },
    credentials: "include",
    signal,
  })
  if (response.status === 401) return "unauthorized"
  if (!response.ok || !response.body) throw new Error(`stream failed: HTTP ${response.status}`)
  onOpen()
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ""
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return "ended"
    const parsed = parseFrames(buffer + value)
    buffer = parsed.rest
    for (const frame of parsed.frames) {
      let message: StreamMessage
      try {
        message = JSON.parse(frame.data) as StreamMessage
      } catch {
        console.warn("[sse] ignoring a frame that is not JSON", frame.data.slice(0, 120))
        continue
      }
      if (message.type === "token_expired") {
        await reader.cancel()
        return "expired"
      }
      onMessage(message)
    }
  }
}

/**
 * Keep an authenticated SSE stream open until `signal` aborts: refreshes the session when the server reports the
 * token expired (or answers 401) and reconnects with backoff after network errors. `path` is re-evaluated on every
 * connection so callers can resume from the last item they saw.
 */
export async function runEventStream(options: {
  path: () => string
  projectId: string
  signal: AbortSignal
  onMessage: (message: StreamMessage) => void
  onStatus?: (status: StreamStatus) => void
}): Promise<void> {
  const { path, projectId, signal, onMessage, onStatus } = options
  let failures = 0
  let authRetries = 0
  while (!signal.aborted) {
    onStatus?.(failures === 0 ? "connecting" : "reconnecting")
    try {
      const end = await readOnce(
        path(),
        projectId,
        signal,
        () => {
          failures = 0
          authRetries = 0
          onStatus?.("live")
        },
        onMessage,
      )
      if (end !== "ended") {
        // A fresh token, then reconnect at once; two 401s in a row fall through to the backoff below.
        if (!(await refreshSession())) {
          useSession.getState().setAnonymous()
          return
        }
        if (end === "expired" || authRetries++ === 0) continue
      }
    } catch (error) {
      if (signal.aborted) return
      console.warn("[sse] stream interrupted; reconnecting", error)
    }
    if (signal.aborted) return
    failures += 1
    onStatus?.(failures > 3 ? "offline" : "reconnecting")
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, backoffDelay(failures))
      signal.addEventListener("abort", () => (clearTimeout(timer), resolve()), { once: true })
    })
  }
}
