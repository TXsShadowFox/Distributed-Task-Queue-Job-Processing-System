# UI review

How the dashboard was checked, what was found and fixed, and what remains. Every page was rendered in a real browser
(Microsoft Edge through Playwright) against the running stack with real data. The screenshots in
[`docs/screenshots/`](../screenshots/) come from `tests/e2e/specs/screenshots.spec.ts`; nothing is mocked.

## Pages

| Page | Highlights | Verified by |
|---|---|---|
| Overview | KPI tiles, throughput chart with a table view, tasks by state, queue depth, live activity, system health | screenshot review, zero console errors |
| Tasks | Search by ID, prefix, idempotency key or type; multi-state filter; type, queue, schedule and run filters; sortable columns; cursor pagination; all of it kept in the URL | `tasks.spec.ts` (filters survive reload, search, row navigation) |
| Task detail | Status, error panel, summary, timeline, attempts with tracebacks, live log tail (SSE) with level filter and download, payload, result, metadata; retry and cancel | `tasks.spec.ts` (live status change, every tab), `cancel a delayed task` |
| Workflows | Cards with last run, recent runs with node progress, run, duplicate, archive | `workflows.spec.ts` |
| Workflow builder | Palette (drag or click), connect by dragging handles, cycle rejection with the offending path, inspector, client and server validation, execution plan by stage, versions with read-only history and restore, `Ctrl/⌘+S`, unsaved-changes guard | `workflows.spec.ts` (build, refused cycle, save, run, versions) |
| Workflow run | Live graph with per-step status and links to step tasks, step table in execution order, cancel | `workflows.spec.ts` (run reaches Succeeded) |
| Workers | Status tiles, slot meter, CPU, memory, uptime, heartbeat, queues; details sheet; drain and resume for platform admins | `schedules-workers.spec.ts` |
| Schedules | Cron editor with presets, server-side description and next five runs in the chosen zone, DST warnings, searchable time-zone picker, misfire and overlap policies, pause and resume, delete; delayed-tasks tab | `schedules-workers.spec.ts` |
| Analytics | Window selector in the URL; throughput, latency over time, slowest types, failure rate by type, errors by type, worker utilization, recent failures | screenshot review |
| Dead letter queue | Filter, inspect sheet (error, last traceback, payload), retry, archive, bulk actions with partial-failure reporting, include archived | `dead-letter.spec.ts` |
| API keys | Scoped creation, secret shown once (cannot be dismissed until the user confirms they stored it), rotation with a grace period, revocation | `api-keys.spec.ts` (old secret 401 after an immediate rotation, revoked key 401) |
| Settings | Profile, password, theme, notifications; organization name, members and roles, projects with retention, role matrix; audit log with pagination | screenshot review, `test_organizations.py` (API) |
| Sign-in, register, 404, error | Validation messages, redirect back after sign-in, branded not-found and error boundaries | `auth.spec.ts` |

## Accessibility

* A skip link to the main content. Landmarks (`nav`, `main`, `header`). One `h1` per page.
* Every form field has a label. Errors are linked with `aria-describedby` and announced (`role="alert"`).
* Status is never shown by colour alone: badges carry text, and charts have legends and a table view.
* Live regions: the task log is `role="log"`; filters and the cron preview are `aria-live="polite"`.
* Destructive actions (cancel, revoke, archive, delete, remove member) need an explicit confirmation dialog.
* Full keyboard support through Radix primitives (menus, dialogs, selects, tabs), plus `Ctrl+K` for the command
  palette and `Ctrl/⌘+S` in the builder.
* `prefers-reduced-motion` disables animations.

## Data visualisation

The charts follow one set of rules:

* Series colours come from a categorical palette validated for colour-vision deficiency, separately for light and
  dark backgrounds.
* Each chart has one y-axis, 2 px lines, bars no wider than 24 px with 4 px rounded ends, a legend when there are
  two or more series, and a crosshair tooltip.
* A series keeps its colour across pages.
* Empty time buckets are drawn as gaps, not interpolated, and isolated points get a marker.

## Responsive and theme checks

* `responsive.spec.ts` (Pixel 7 viewport) opens every page from the mobile navigation and asserts the document never
  scrolls horizontally. Wide tables scroll inside their cards.
* Light theme was reviewed by screenshot (Overview, Analytics, Workflow run). Both themes use the same validated
  chart palette, adjusted for each background.

## Issues found and fixed

| Issue | Fix |
|---|---|
| Next.js 16 kept the previous page mounted but hidden (`cacheComponents`), so selectors matched hidden duplicates | Tests use role-based locators, which ignore hidden elements; pages unmount their effects when hidden |
| Filters and tabs lagged a router round trip behind clicks | URL state uses native `history.replaceState`, which Next.js syncs with `useSearchParams` |
| The latency chart drew straight lines across empty time buckets | The API gap-fills the series; the chart draws gaps and marks isolated points |
| Duration labels read "1m 60s" and "<1 ms" for zero | Rounding fixed; zero reads "0 ms" |
| Y-axis labels such as "500 ms" were clipped | The axis width adapts to the formatter |
| Workflow graphs were unreadably small (320 px spacing, 224 px nodes) | 176 px nodes, 240 px spacing, collapsible palette, minimap hidden on small screens |
| Clicking several task types stacked new steps on top of each other | Clicked steps go to the right of the rightmost step, and the view refits |
| After a dropped SSE connection, the UI could stay stale until the next refetch | Reconnecting invalidates every cached query of the project |
| Settings text said "a owner" and "history kept the platform default of days" | Corrected |
| Dev-only indicator overlapped the sidebar | Moved to the bottom-right corner; hidden in README screenshots |

## Remaining

* The workflow builder is usable on a phone but designed for desktop.
* The CSP allows inline scripts (see [known issues](known-issues.md)).
* No automated contrast audit (axe) runs in CI yet. Contrast was checked for the chart palette by script and for the
  rest by review.
