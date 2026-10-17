"use client"

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query"

import { api } from "@/lib/api/client"
import type {
  AnalyticsWindow,
  ApiKey,
  ApiKeySecret,
  Attempt,
  AuditLogList,
  BulkResult,
  CronPreview,
  Failures,
  GraphIn,
  Latency,
  LogList,
  Me,
  Member,
  NotificationList,
  Organization,
  Overview,
  Project,
  ProjectAccess,
  RoleMatrix,
  RunDetail,
  RunSummary,
  Schedule,
  ScheduleIn,
  SchedulePatch,
  SystemStatus,
  TaskEvent,
  TaskList,
  TaskOut,
  TaskSubmit,
  TaskType,
  Throughput,
  Utilization,
  ValidateOut,
  Worker,
  WorkerDetail,
  Workflow,
  WorkflowDetail,
} from "@/lib/api/types"
import { useSession } from "@/lib/session"

/** Every project-scoped query key starts with ["p", projectId] so switching projects never mixes caches. */
export function useProjectKey(...parts: unknown[]): QueryKey {
  const projectId = useSession((s) => s.projectId)
  return ["p", projectId, ...parts]
}

function useProjectReady(): boolean {
  return useSession((s) => s.status === "authenticated" && !!s.projectId)
}

function useInvalidate() {
  const queryClient = useQueryClient()
  const projectId = useSession((s) => s.projectId)
  return (...parts: unknown[]) => queryClient.invalidateQueries({ queryKey: ["p", projectId, ...parts] })
}

// ------------------------------------------------------------------------------------------------ tasks
export interface TaskFilters {
  state?: string[]
  task_type?: string
  queue?: string
  q?: string
  workflow_run_id?: string
  schedule_id?: string
  sort?: "created_at" | "updated_at" | "priority"
  order?: "asc" | "desc"
  limit?: number
  cursor?: string | null
}

export function useTasks(filters: TaskFilters, options: { refetchInterval?: number } = {}) {
  return useQuery({
    queryKey: useProjectKey("tasks", filters),
    queryFn: ({ signal }) =>
      api<TaskList>("/tasks", { signal, query: { ...filters, include_total: true } as never }),
    enabled: useProjectReady(),
    placeholderData: keepPreviousData,
    refetchInterval: options.refetchInterval,
  })
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: useProjectKey("task", taskId),
    queryFn: ({ signal }) => api<TaskOut>(`/tasks/${taskId}`, { signal }),
    enabled: useProjectReady() && !!taskId,
  })
}

export function useTaskAttempts(taskId: string) {
  return useQuery({
    queryKey: useProjectKey("task", taskId, "attempts"),
    queryFn: ({ signal }) => api<Attempt[]>(`/tasks/${taskId}/attempts`, { signal }),
    enabled: useProjectReady() && !!taskId,
  })
}

export function useTaskEvents(taskId: string) {
  return useQuery({
    queryKey: useProjectKey("task", taskId, "events"),
    queryFn: ({ signal }) => api<TaskEvent[]>(`/tasks/${taskId}/events`, { signal }),
    enabled: useProjectReady() && !!taskId,
  })
}

export function useTaskLogs(taskId: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: useProjectKey("task", taskId, "logs"),
    queryFn: ({ signal }) => api<LogList>(`/tasks/${taskId}/logs`, { signal, query: { limit: 2000 } }),
    enabled: useProjectReady() && !!taskId && enabled,
  })
}

export function useTaskTypes() {
  return useQuery({
    queryKey: useProjectKey("task-types"),
    queryFn: ({ signal }) => api<TaskType[]>("/task-types", { signal }),
    enabled: useProjectReady(),
    staleTime: 60_000,
  })
}

export function useSubmitTask() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: TaskSubmit; idempotencyKey?: string }) =>
      api<TaskOut>("/tasks", {
        method: "POST",
        body,
        headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      }),
    onSuccess: () => {
      void invalidate("tasks")
      void invalidate("analytics")
    },
  })
}

function useTaskAction(action: "cancel" | "retry", base: "/tasks" | "/dead-letter" = "/tasks") {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (taskId: string) => api<TaskOut>(`${base}/${taskId}/${action}`, { method: "POST" }),
    onSuccess: (task) => {
      void invalidate("task", task.id)
      void invalidate("tasks")
      void invalidate("dead-letter")
      void invalidate("runs")
    },
  })
}

export const useCancelTask = () => useTaskAction("cancel")
export const useRetryTask = () => useTaskAction("retry")

// ------------------------------------------------------------------------------------------------ dead letter
export function useDeadLetter(params: { task_type?: string; include_archived?: boolean; cursor?: string | null }) {
  return useQuery({
    queryKey: useProjectKey("dead-letter", params),
    queryFn: ({ signal }) => api<TaskList>("/dead-letter", { signal, query: { ...params, limit: 50 } as never }),
    enabled: useProjectReady(),
    placeholderData: keepPreviousData,
  })
}

export function useArchiveTask() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (taskId: string) => api<TaskOut>(`/dead-letter/${taskId}/archive`, { method: "POST" }),
    onSuccess: () => void invalidate("dead-letter"),
  })
}

export function useBulkDeadLetter(action: "retry" | "archive") {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (taskIds: string[]) =>
      api<BulkResult>(`/dead-letter/${action}`, { method: "POST", body: { task_ids: taskIds } }),
    onSuccess: () => {
      void invalidate("dead-letter")
      void invalidate("tasks")
    },
  })
}

// ------------------------------------------------------------------------------------------------ workflows
export function useWorkflows(includeArchived = false) {
  return useQuery({
    queryKey: useProjectKey("workflows", includeArchived),
    queryFn: ({ signal }) =>
      api<Workflow[]>("/workflows", { signal, query: { include_archived: includeArchived } }),
    enabled: useProjectReady(),
  })
}

export function useWorkflow(workflowId: string, version?: number) {
  return useQuery({
    queryKey: useProjectKey("workflow", workflowId, version ?? "latest"),
    queryFn: ({ signal }) => api<WorkflowDetail>(`/workflows/${workflowId}`, { signal, query: { version } }),
    enabled: useProjectReady() && !!workflowId,
  })
}

export function useCreateWorkflow() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: { name: string; description?: string | null; graph: GraphIn }) =>
      api<WorkflowDetail>("/workflows", { method: "POST", body }),
    onSuccess: () => void invalidate("workflows"),
  })
}

export function useSaveWorkflow(workflowId: string) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: { graph: GraphIn; expected_version?: number; name?: string; description?: string | null }) =>
      api<WorkflowDetail>(`/workflows/${workflowId}`, { method: "PUT", body }),
    onSuccess: () => {
      void invalidate("workflow", workflowId)
      void invalidate("workflows")
    },
  })
}

export function useArchiveWorkflow() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (workflowId: string) => api<Workflow>(`/workflows/${workflowId}`, { method: "DELETE" }),
    onSuccess: () => void invalidate("workflows"),
  })
}

export function useValidateWorkflow() {
  return useMutation({
    mutationFn: (graph: GraphIn) => api<ValidateOut>("/workflows/validate", { method: "POST", body: graph }),
  })
}

export function useExecuteWorkflow(workflowId: string) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: { input: Record<string, unknown>; version?: number }) =>
      api<RunDetail>(`/workflows/${workflowId}/execute`, { method: "POST", body }),
    onSuccess: () => {
      void invalidate("runs")
      void invalidate("workflows")
    },
  })
}

export function useWorkflowRuns(workflowId?: string, stateFilter?: string) {
  return useQuery({
    queryKey: useProjectKey("runs", workflowId ?? "all", stateFilter ?? "any"),
    queryFn: ({ signal }) =>
      api<{ items: RunSummary[] }>(workflowId ? `/workflows/${workflowId}/runs` : "/workflow-runs", {
        signal,
        query: { state: stateFilter, limit: 50 },
      }),
    enabled: useProjectReady(),
  })
}

export function useWorkflowRun(runId: string) {
  return useQuery({
    queryKey: useProjectKey("runs", "detail", runId),
    queryFn: ({ signal }) => api<RunDetail>(`/workflow-runs/${runId}`, { signal }),
    enabled: useProjectReady() && !!runId,
  })
}

export function useCancelRun() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (runId: string) => api<RunDetail>(`/workflow-runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      void invalidate("runs")
      void invalidate("tasks")
    },
  })
}

// ------------------------------------------------------------------------------------------------ workers
export function useWorkers() {
  return useQuery({
    queryKey: useProjectKey("workers"),
    queryFn: ({ signal }) => api<Worker[]>("/workers", { signal }),
    enabled: useProjectReady(),
    refetchInterval: 10_000,
  })
}

export function useWorkerDetail(workerId: string | null) {
  return useQuery({
    queryKey: useProjectKey("workers", workerId),
    queryFn: ({ signal }) => api<WorkerDetail>(`/workers/${workerId}`, { signal }),
    enabled: useProjectReady() && !!workerId,
  })
}

export function useWorkerDrain() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ workerId, drain }: { workerId: string; drain: boolean }) =>
      api<Worker>(`/workers/${workerId}/${drain ? "drain" : "resume"}`, { method: "POST" }),
    onSuccess: () => void invalidate("workers"),
  })
}

// ------------------------------------------------------------------------------------------------ schedules
export function useSchedules() {
  return useQuery({
    queryKey: useProjectKey("schedules"),
    queryFn: ({ signal }) => api<Schedule[]>("/schedules", { signal }),
    enabled: useProjectReady(),
  })
}

export function useCreateSchedule() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: ScheduleIn) => api<Schedule>("/schedules", { method: "POST", body }),
    onSuccess: () => void invalidate("schedules"),
  })
}

export function useUpdateSchedule() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: SchedulePatch }) =>
      api<Schedule>(`/schedules/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => void invalidate("schedules"),
  })
}

export function useDeleteSchedule() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/schedules/${id}`, { method: "DELETE" }),
    onSuccess: () => void invalidate("schedules"),
  })
}

export function useCronPreview(expression: string, timezone: string) {
  return useQuery({
    queryKey: ["cron-preview", expression, timezone],
    queryFn: ({ signal }) =>
      api<CronPreview>("/schedules/preview", {
        method: "POST",
        signal,
        body: { cron_expression: expression, timezone, count: 5 },
      }),
    enabled: useProjectReady() && expression.trim().length > 0 && timezone.length > 0,
    retry: false,
    staleTime: 30_000,
  })
}

// ------------------------------------------------------------------------------------------------ analytics
export function useOverview() {
  return useQuery({
    queryKey: useProjectKey("analytics", "overview"),
    queryFn: ({ signal }) => api<Overview>("/analytics/overview", { signal }),
    enabled: useProjectReady(),
    refetchInterval: 15_000,
  })
}

export function useThroughput(window: AnalyticsWindow) {
  return useQuery({
    queryKey: useProjectKey("analytics", "throughput", window),
    queryFn: ({ signal }) => api<Throughput>("/analytics/throughput", { signal, query: { window } }),
    enabled: useProjectReady(),
    refetchInterval: 30_000,
  })
}

export function useLatency(window: AnalyticsWindow) {
  return useQuery({
    queryKey: useProjectKey("analytics", "latency", window),
    queryFn: ({ signal }) => api<Latency>("/analytics/latency", { signal, query: { window } }),
    enabled: useProjectReady(),
  })
}

export function useFailures(window: AnalyticsWindow) {
  return useQuery({
    queryKey: useProjectKey("analytics", "failures", window),
    queryFn: ({ signal }) => api<Failures>("/analytics/failures", { signal, query: { window } }),
    enabled: useProjectReady(),
  })
}

export function useUtilization(window: AnalyticsWindow) {
  return useQuery({
    queryKey: useProjectKey("analytics", "workers", window),
    queryFn: ({ signal }) => api<Utilization>("/analytics/workers", { signal, query: { window } }),
    enabled: useProjectReady(),
  })
}

export function useSystemStatus() {
  return useQuery({
    queryKey: ["system-status"],
    queryFn: ({ signal }) => api<SystemStatus>("/system/status", { signal, project: false }),
    enabled: useSession((s) => s.status === "authenticated"),
    refetchInterval: 15_000,
  })
}

// ------------------------------------------------------------------------------------------------ API keys
export function useApiKeys(enabled = true) {
  return useQuery({
    queryKey: useProjectKey("api-keys"),
    queryFn: ({ signal }) => api<ApiKey[]>("/api-keys", { signal }),
    enabled: useProjectReady() && enabled,
  })
}

export function useCreateApiKey() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: { name: string; scopes: string[]; expires_in_days?: number | null }) =>
      api<ApiKeySecret>("/api-keys", { method: "POST", body }),
    onSuccess: () => void invalidate("api-keys"),
  })
}

export function useRotateApiKey() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ id, grace }: { id: string; grace: number }) =>
      api<ApiKeySecret>(`/api-keys/${id}/rotate`, { method: "POST", body: { grace_period_seconds: grace } }),
    onSuccess: () => void invalidate("api-keys"),
  })
}

export function useRevokeApiKey() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (id: string) => api<ApiKey>(`/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => void invalidate("api-keys"),
  })
}

export function useRoleMatrix() {
  return useQuery({
    queryKey: ["role-matrix"],
    queryFn: ({ signal }) => api<RoleMatrix>("/organizations/roles", { signal, project: false }),
    staleTime: Infinity,
    enabled: useSession((s) => s.status === "authenticated"),
  })
}

// ------------------------------------------------------------------------------------------------ notifications
export function useNotifications() {
  return useQuery({
    queryKey: useProjectKey("notifications"),
    queryFn: ({ signal }) => api<NotificationList>("/notifications", { signal }),
    enabled: useProjectReady(),
    refetchInterval: 30_000,
  })
}

export function useMarkNotificationsRead() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: () => api<{ ok: boolean }>("/notifications/read", { method: "POST" }),
    onSuccess: () => void invalidate("notifications"),
  })
}

// ------------------------------------------------------------------------------------------------ account & org
export function useUpdateMe() {
  return useMutation({
    mutationFn: (body: { full_name?: string; preferences?: Record<string, unknown> }) =>
      api<Me>("/auth/me", { method: "PATCH", body, project: false }),
    onSuccess: (me) => useSession.getState().setMe(me),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api<{ ok: boolean }>("/auth/change-password", { method: "POST", body, project: false }),
  })
}

export function useOrganization(orgId?: string) {
  return useQuery({
    queryKey: ["org", orgId],
    queryFn: ({ signal }) => api<Organization>(`/organizations/${orgId}`, { signal, project: false }),
    enabled: !!orgId,
  })
}

export function useUpdateOrganization(orgId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      api<Organization>(`/organizations/${orgId}`, { method: "PATCH", body: { name }, project: false }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["org", orgId] }),
  })
}

export function useMembers(orgId?: string) {
  return useQuery({
    queryKey: ["org", orgId, "members"],
    queryFn: ({ signal }) => api<Member[]>(`/organizations/${orgId}/members`, { signal, project: false }),
    enabled: !!orgId,
  })
}

export function useMemberMutations(orgId?: string) {
  const queryClient = useQueryClient()
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["org", orgId, "members"] })
  return {
    add: useMutation({
      mutationFn: (body: { email: string; role: string }) =>
        api<Member>(`/organizations/${orgId}/members`, { method: "POST", body, project: false }),
      onSuccess: refresh,
    }),
    update: useMutation({
      mutationFn: ({ memberId, role }: { memberId: string; role: string }) =>
        api<Member>(`/organizations/${orgId}/members/${memberId}`, {
          method: "PATCH",
          body: { role },
          project: false,
        }),
      onSuccess: refresh,
    }),
    remove: useMutation({
      mutationFn: (memberId: string) =>
        api<void>(`/organizations/${orgId}/members/${memberId}`, { method: "DELETE", project: false }),
      onSuccess: refresh,
    }),
  }
}

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api<ProjectAccess[]>("/projects", { signal, project: false }),
    enabled: useSession((s) => s.status === "authenticated"),
  })
}

export function useProjectMutations() {
  const queryClient = useQueryClient()
  const refreshMe = async () => {
    await queryClient.invalidateQueries({ queryKey: ["projects"] })
    const me = await api<Me>("/auth/me", { project: false })
    useSession.getState().setMe(me)
  }
  return {
    create: useMutation({
      mutationFn: ({ orgId, name, description }: { orgId: string; name: string; description?: string }) =>
        api<Project>(`/organizations/${orgId}/projects`, {
          method: "POST",
          body: { name, description },
          project: false,
        }),
      onSuccess: refreshMe,
    }),
    update: useMutation({
      mutationFn: ({ projectId, ...body }: { projectId: string; name?: string; description?: string; settings?: Record<string, unknown> }) =>
        api<Project>(`/projects/${projectId}`, { method: "PATCH", body, project: false }),
      onSuccess: refreshMe,
    }),
  }
}

export function useAuditLogs(orgId: string | undefined, enabled: boolean, beforeId?: number) {
  return useQuery({
    queryKey: ["org", orgId, "audit", beforeId ?? "first"],
    queryFn: ({ signal }) =>
      api<AuditLogList>(`/organizations/${orgId}/audit-logs`, {
        signal,
        project: false,
        query: { before_id: beforeId, limit: 50 },
      }),
    enabled: !!orgId && enabled,
    placeholderData: keepPreviousData,
  })
}
