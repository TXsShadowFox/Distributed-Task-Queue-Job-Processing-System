"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { create } from "zustand"

import type { RealtimeEvent, TaskOut } from "@/lib/api/types"
import { useSession } from "@/lib/session"
import { runEventStream, type StreamStatus } from "@/lib/sse"

export type RealtimeStatus = StreamStatus

export interface ActivityItem {
  id: string
  type: string
  ts: number
  data: Record<string, unknown>
}

interface RealtimeState {
  status: RealtimeStatus
  activity: ActivityItem[]
  setStatus: (status: RealtimeStatus) => void
  push: (item: ActivityItem) => void
  clear: () => void
}

const MAX_ACTIVITY = 60
let sequence = 0

export const useRealtime = create<RealtimeState>((set) => ({
  status: "connecting",
  activity: [],
  setStatus: (status) => set({ status }),
  push: (item) => set((s) => ({ activity: [item, ...s.activity].slice(0, MAX_ACTIVITY) })),
  clear: () => set({ activity: [] }),
}))

/**
 * Keeps one SSE connection per project open while the dashboard is mounted. Events invalidate the affected queries
 * (coalesced: at most one refetch per resource per ~750 ms), so the UI always re-reads authoritative state from the
 * API instead of trusting event payloads.
 */
export function useRealtimeConnection() {
  const queryClient = useQueryClient()
  const projectId = useSession((s) => s.projectId)
  const authenticated = useSession((s) => s.status === "authenticated")

  useEffect(() => {
    if (!authenticated || !projectId) return
    const controller = new AbortController()
    const { setStatus, push } = useRealtime.getState()
    const dirty = new Set<string>()
    let flushTimer: ReturnType<typeof setTimeout> | null = null

    const markDirty = (...key: unknown[]) => {
      dirty.add(JSON.stringify(key))
      flushTimer ??= setTimeout(() => {
        for (const k of dirty) {
          void queryClient.invalidateQueries({ queryKey: ["p", projectId, ...(JSON.parse(k) as unknown[])] })
        }
        dirty.clear()
        flushTimer = null
      }, 750)
    }

    let connectedBefore = false

    const handle = (event: RealtimeEvent) => {
      const data = event.data ?? {}
      switch (event.type) {
        case "ready":
          // After a reconnect, events from the gap are gone: refetch everything instead of trusting the cache.
          if (connectedBefore) void queryClient.invalidateQueries({ queryKey: ["p", projectId] })
          connectedBefore = true
          return
        case "resync":
          void queryClient.invalidateQueries({ queryKey: ["p", projectId] })
          return
        case "task.updated":
          markDirty("tasks")
          markDirty("analytics")
          markDirty("dead-letter")
          markDirty("notifications")
          if (typeof data.id === "string") markDirty("task", data.id)
          if (data.workflow_run_id) markDirty("runs")
          break
        case "task.progress":
          if (typeof data.id === "string") {
            queryClient.setQueryData<TaskOut>(["p", projectId, "task", data.id], (old) =>
              old ? { ...old, progress: Number(data.progress), progress_message: (data.message as string) ?? null } : old,
            )
          }
          return
        case "workflow_run.updated":
          markDirty("runs")
          markDirty("workflows")
          break
        case "worker.updated":
          markDirty("workers")
          return // heartbeats are too chatty for the activity feed
        case "schedule.updated":
          markDirty("schedules")
          break
        default:
          return
      }
      push({ id: `${Date.now()}-${sequence++}`, type: event.type, ts: Date.now(), data })
    }

    void runEventStream({
      path: () => "/events/stream",
      projectId,
      signal: controller.signal,
      onStatus: setStatus,
      onMessage: (message) => handle(message as unknown as RealtimeEvent),
    })
    return () => {
      controller.abort()
      if (flushTimer) clearTimeout(flushTimer)
    }
  }, [authenticated, projectId, queryClient])
}
