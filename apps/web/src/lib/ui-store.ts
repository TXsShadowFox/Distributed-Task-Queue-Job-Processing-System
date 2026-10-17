"use client"

import { create } from "zustand"

const SIDEBAR_KEY = "ff-sidebar-collapsed"

function storedCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1"
  } catch {
    return false
  }
}

interface UiState {
  sidebarCollapsed: boolean
  mobileNavOpen: boolean
  commandOpen: boolean
  submitTaskOpen: boolean
  submitTaskType: string | null
  toggleSidebar: () => void
  setMobileNav: (open: boolean) => void
  setCommand: (open: boolean) => void
  openSubmitTask: (taskType?: string | null) => void
  closeSubmitTask: () => void
}

export const useUi = create<UiState>((set, get) => ({
  sidebarCollapsed: typeof window === "undefined" ? false : storedCollapsed(),
  mobileNavOpen: false,
  commandOpen: false,
  submitTaskOpen: false,
  submitTaskType: null,
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0")
    } catch {
      // Storage unavailable: preference applies for this session only.
    }
    set({ sidebarCollapsed: next })
  },
  setMobileNav: (open) => set({ mobileNavOpen: open }),
  setCommand: (open) => set({ commandOpen: open }),
  openSubmitTask: (taskType = null) => set({ submitTaskOpen: true, submitTaskType: taskType }),
  closeSubmitTask: () => set({ submitTaskOpen: false }),
}))
