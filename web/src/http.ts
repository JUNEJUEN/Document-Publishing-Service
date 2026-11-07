/** The parsed JSON body, or null when the body is not JSON. */
export function readJson(res: Response): Promise<unknown> {
  return res.json().catch(() => null)
}

/** One field of a JSON response body; undefined when the body is not a JSON object. */
export async function jsonField(res: Response, key: string): Promise<unknown> {
  const body = await readJson(res)
  return typeof body === 'object' && body !== null ? (body as Record<string, unknown>)[key] : undefined
}

/** A message for the user, for any error thrown by an API call. */
export function errorMessage(e: unknown): string {
  // fetch() throws a TypeError when the network request itself fails.
  if (e instanceof TypeError) return 'Cannot reach the API. Please try again.'
  return e instanceof Error ? e.message : String(e)
}
