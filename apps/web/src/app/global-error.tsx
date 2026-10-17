"use client"

/**
 * Last-resort boundary for errors in the root layout. It replaces the whole document, so it cannot rely on the
 * app's stylesheet or theme: styles are inline and dark, matching the default theme.
 */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100dvh",
          display: "grid",
          placeItems: "center",
          background: "#0b0d17",
          color: "#e6e8f0",
          fontFamily: "system-ui, sans-serif",
          padding: 16,
        }}
      >
        <title>FlowForge · Error</title>
        <main role="alert" style={{ maxWidth: 420, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, margin: 0 }}>FlowForge could not load</h1>
          <p style={{ color: "#9aa0b4", fontSize: 14 }}>An unexpected error stopped the app from rendering. Nothing was changed.</p>
          {error.digest && <p style={{ color: "#9aa0b4", fontFamily: "monospace", fontSize: 12 }}>Reference {error.digest}</p>}
          <button
            type="button"
            onClick={() => retry()}
            style={{ marginTop: 16, padding: "8px 16px", borderRadius: 8, border: "none", background: "#22d3ee", color: "#04121a", fontWeight: 600, cursor: "pointer" }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  )
}
