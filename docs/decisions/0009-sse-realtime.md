# 0009. Server-Sent Events over fetch for realtime updates

**Status:** Accepted

## Context

The dashboard needs live task, workflow and worker updates, and live log tails. All of this traffic flows from server
to client.

## Decision

SSE endpoints (`/events/stream`, `/tasks/{id}/logs/stream`) are fed by Redis pub/sub. The client reads them with
`fetch` rather than `EventSource`, so the access token travels in the `Authorization` header instead of the URL.
Events only *invalidate* cached queries, coalesced to at most one refetch per resource every 750 ms; the UI always
re-reads authoritative state from the API.

## Consequences

* Works through ordinary HTTP proxies (nginx disables buffering on these paths), with no WebSocket upgrade.
* A lost event cannot leave the UI wrong: the next refetch corrects it, and a reconnect triggers a full resync.
* A stream ends when its access token expires; the client refreshes the token and reconnects with backoff.
