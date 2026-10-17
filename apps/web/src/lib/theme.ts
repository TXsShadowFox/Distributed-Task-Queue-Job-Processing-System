"use client"

import { useSyncExternalStore } from "react"

export type ThemePreference = "dark" | "light" | "system"
export const THEME_STORAGE_KEY = "ff-theme"

/**
 * Runs synchronously in <head> before the first paint (no theme flash). Dark is the default; "system" follows
 * prefers-color-scheme. Kept tiny and dependency-free because it is inlined into every page.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var p=localStorage.getItem("${THEME_STORAGE_KEY}")||"dark";var d=p==="system"?window.matchMedia("(prefers-color-scheme: dark)").matches:p!=="light";var e=document.documentElement;e.classList.toggle("dark",d);e.dataset.theme=d?"dark":"light";}catch(_){document.documentElement.classList.add("dark");}})()`

const listeners = new Set<() => void>()

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY)
    return value === "light" || value === "system" ? value : "dark"
  } catch {
    return "dark"
  }
}

function resolve(preference: ThemePreference): "dark" | "light" {
  if (preference === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  }
  return preference
}

function apply(preference: ThemePreference) {
  const resolved = resolve(preference)
  const root = document.documentElement
  root.classList.toggle("dark", resolved === "dark")
  root.dataset.theme = resolved
}

export function setThemePreference(preference: ThemePreference) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // Storage may be unavailable (private mode); the theme still applies for this page view.
  }
  apply(preference)
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  const media = window.matchMedia("(prefers-color-scheme: dark)")
  const onMedia = () => {
    if (readPreference() === "system") {
      apply("system")
      fn()
    }
  }
  media.addEventListener("change", onMedia)
  return () => {
    listeners.delete(fn)
    media.removeEventListener("change", onMedia)
  }
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, readPreference, () => "dark")
}

export function useResolvedTheme(): "dark" | "light" {
  return useSyncExternalStore(
    subscribe,
    () => (document.documentElement.classList.contains("dark") ? "dark" : "light"),
    () => "dark",
  )
}
