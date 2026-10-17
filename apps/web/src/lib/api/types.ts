/**
 * Friendly aliases over the types generated from the API's OpenAPI document (`npm run gen:api`), plus the analytics
 * payloads, which the API returns as plain JSON objects.
 */
import type { components } from "./schema"

type S = components["schemas"]

export type TaskOut = S["TaskOut"]
export type TaskSummary = S["TaskSummaryOut"]
export type TaskList = S["TaskListOut"]
export type TaskSubmit = S["TaskSubmit"]
export type TaskType = S["TaskTypeOut"]
export type Attempt = S["AttemptOut"]
export type TaskEvent = S["EventOut"]
export type TaskLog = S["LogOut"]
export type LogList = S["LogListOut"]
export type BulkResult = S["BulkResultOut"]

export type TokenOut = S["TokenOut"]
export type User = S["UserOut"]
export type Me = S["MeOut"]
export type Membership = S["MembershipOut"]
export type Project = S["ProjectOut"]
export type ProjectAccess = S["ProjectAccessOut"]
export type Organization = S["OrganizationOut"]
export type Member = S["MemberOut"]
export type AuditLog = S["AuditLogOut"]
export type AuditLogList = S["AuditLogListOut"]
export type RoleMatrix = S["RoleMatrixOut"]
export type Preferences = S["PreferencesIn"]

export type ApiKey = S["ApiKeyOut"]
export type ApiKeySecret = S["ApiKeySecretOut"]

export type Worker = S["WorkerOut"]
export type WorkerDetail = S["WorkerDetailOut"]

export type Schedule = S["ScheduleOut"]
export type ScheduleIn = S["ScheduleIn"]
export type SchedulePatch = S["SchedulePatch"]
export type CronPreview = S["PreviewOut"]

export type Workflow = S["WorkflowOut"]
export type WorkflowDetail = S["WorkflowDetailOut"]
export type GraphIn = S["GraphIn"]
export type NodeIn = S["NodeIn"]
export type EdgeIn = S["EdgeIn"]
export type NodeOut = S["NodeOut"]
export type ValidateOut = S["ValidateOut"]
export type RunSummary = S["RunSummaryOut"]
export type RunDetail = S["RunDetailOut"]
export type NodeRun = S["NodeRunOut"]

export type Notification = S["NotificationOut"]
export type NotificationList = S["NotificationListOut"]

export type TaskState =
  | "CREATED"
  | "SCHEDULED"
  | "QUEUED"
  | "RUNNING"
  | "RETRY_SCHEDULED"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCEL_REQUESTED"
  | "CANCELLED"
  | "DEAD_LETTERED"

export const TASK_STATES: TaskState[] = [
  "QUEUED",
  "SCHEDULED",
  "RUNNING",
  "RETRY_SCHEDULED",
  "CANCEL_REQUESTED",
  "SUCCEEDED",
  "FAILED",
  "DEAD_LETTERED",
  "CANCELLED",
]

export const TERMINAL_STATES = new Set<string>(["SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTERED"])
export const RETRYABLE_STATES = new Set<string>(["FAILED", "CANCELLED", "DEAD_LETTERED"])
export const CANCELLABLE_STATES = new Set<string>(["CREATED", "SCHEDULED", "QUEUED", "RETRY_SCHEDULED", "RUNNING"])

export type AnalyticsWindow = "1h" | "6h" | "24h" | "7d" | "30d"

export interface Overview {
  tasks_by_state: Record<string, number>
  total_tasks: number
  running: number
  queued: number
  scheduled: number
  succeeded_24h: number
  failed_24h: number
  cancelled_24h: number
  success_rate_24h: number | null
  retries_24h: number
  avg_duration_ms_24h: number | null
  throughput_per_min_1h: number
  queue_depth: { queue: string; queued: number; oldest_queued_at: string | null }[]
  dead_letter_open: number
  workers: { active: number; draining: number; offline: number; capacity: number; busy_slots: number }
  generated_at: string
}

export interface ThroughputPoint {
  ts: string
  submitted: number
  succeeded: number
  failed: number
  cancelled: number
  retried: number
}

export interface Throughput {
  window: AnalyticsWindow
  bucket_seconds: number
  series: ThroughputPoint[]
}

export interface Percentiles {
  p50_ms: number | null
  p95_ms: number | null
  p99_ms: number | null
  avg_ms: number | null
  max_ms: number | null
  count: number
}

export interface Latency {
  window: AnalyticsWindow
  execution: Percentiles
  queue_wait: Percentiles
  end_to_end: Percentiles
  by_task_type: (Percentiles & { task_type: string })[]
  bucket_seconds: number
  series: { ts: string; exec_p50_ms: number | null; exec_p95_ms: number | null; wait_p95_ms: number | null }[]
}

export interface Failures {
  window: AnalyticsWindow
  by_task_type: { task_type: string; total: number; failed: number; failure_rate: number }[]
  by_error_type: { error_type: string; attempt_status: string; count: number }[]
  recent: {
    task_id: string
    task_type: string
    state: string
    error_type: string | null
    message: string
    finished_at: string
  }[]
}

export interface Utilization {
  window: AnalyticsWindow
  workers: {
    worker_id: string
    name: string
    status: string
    concurrency: number
    attempts: number
    busy_seconds: number
    /** Seconds the worker was online within the window (utilization is relative to this). */
    online_seconds: number
    utilization: number
  }[]
}

export interface SystemStatus {
  status: "operational" | "degraded" | "outage"
  database: boolean
  redis: boolean
  realtime: boolean
  workers: Record<string, number>
  outbox_backlog: number | null
}

export interface RealtimeEvent {
  type: string
  project_id?: string | null
  data: Record<string, unknown>
  ts?: string
}
