/**
 * Replace the query string without a navigation. Next.js syncs native history calls with useSearchParams, so
 * filters and tabs update at once instead of waiting for a router round trip.
 */
export function replaceQuery(pathname: string, query: URLSearchParams | string) {
  const search = typeof query === "string" ? query : query.toString()
  window.history.replaceState(null, "", search ? `${pathname}?${search}` : pathname)
}
