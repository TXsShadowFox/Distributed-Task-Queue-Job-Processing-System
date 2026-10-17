"use client"

import { create } from "zustand"

import type { Me, Membership, TokenOut, User } from "@/lib/api/types"

const PROJECT_KEY = "ff-project"

export type SessionStatus = "unknown" | "authenticated" | "anonymous"

interface SessionState {
  status: SessionStatus
  /** Access token lives only in memory; the refresh token is an httpOnly cookie the page can never read. */
  accessToken: string | null
  expiresAt: number | null
  user: User | null
  memberships: Membership[]
  projectId: string | null
  setToken: (token: TokenOut) => void
  setMe: (me: Me) => void
  setProject: (projectId: string) => void
  setAnonymous: () => void
}

function storedProject(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY)
  } catch {
    return null
  }
}

export const useSession = create<SessionState>((set, get) => ({
  status: "unknown",
  accessToken: null,
  expiresAt: null,
  user: null,
  memberships: [],
  projectId: null,
  setToken: (token) =>
    set({
      accessToken: token.access_token,
      expiresAt: Date.parse(token.expires_at),
      user: token.user,
    }),
  setMe: (me) => {
    const projects = me.memberships.flatMap((m) => m.projects.map((p) => p.id))
    const preferred = [get().projectId, storedProject(), me.user.preferences?.default_project_id as string | undefined]
    const projectId = preferred.find((id): id is string => !!id && projects.includes(id)) ?? projects[0] ?? null
    set({ user: me.user, memberships: me.memberships, projectId, status: "authenticated" })
  },
  setProject: (projectId) => {
    try {
      localStorage.setItem(PROJECT_KEY, projectId)
    } catch {
      // Storage unavailable: the selection still applies for this tab.
    }
    set({ projectId })
  },
  setAnonymous: () =>
    set({ status: "anonymous", accessToken: null, expiresAt: null, user: null, memberships: [] }),
}))

export function currentMembership(state: Pick<SessionState, "memberships" | "projectId">): Membership | undefined {
  return state.memberships.find((m) => m.projects.some((p) => p.id === state.projectId))
}

export function useCurrentMembership(): Membership | undefined {
  return useSession((s) => currentMembership(s))
}

export function useCurrentProject() {
  return useSession((s) => {
    for (const m of s.memberships) {
      const project = m.projects.find((p) => p.id === s.projectId)
      if (project) return project
    }
    return undefined
  })
}

const ROLE_RANK: Record<string, number> = { viewer: 0, developer: 1, admin: 2, owner: 3 }

/** Mirrors the server's RBAC matrix for *display* decisions only; the API enforces every permission itself. */
export function useCan() {
  const role = useCurrentMembership()?.role ?? "viewer"
  const platformAdmin = useSession((s) => s.user?.is_platform_admin ?? false)
  const rank = ROLE_RANK[role] ?? 0
  return {
    role,
    write: rank >= 1,
    admin: rank >= 2,
    owner: rank >= 3,
    /** Operators of the whole installation (shared infrastructure such as workers). */
    platformAdmin,
  }
}
