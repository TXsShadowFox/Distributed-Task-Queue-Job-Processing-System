/** Parse a JSON object typed by a user (payloads, run inputs, metadata). Empty text means {}. */
export function parseJsonObject(text: string, label = "Value"): { value?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { value: {} }
  try {
    const parsed = JSON.parse(text) as unknown
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return { error: `${label} must be a JSON object` }
    return { value: parsed as Record<string, unknown> }
  } catch (error) {
    return { error: `Invalid JSON: ${(error as Error).message}` }
  }
}

export const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2)
