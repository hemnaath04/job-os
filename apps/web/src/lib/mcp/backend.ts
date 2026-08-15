/**
 * MCP tool handlers call the same FastAPI backend the web app's proxy uses,
 * forwarding the caller's verified Clerk OAuth access token as-is.
 *
 * That token is a Clerk-issued JWT with the same `sub` (Clerk user id) a
 * normal session token carries, so job_os.auth.get_current_user resolves it
 * to the same account the user already has on the web app, no backend
 * changes needed.
 */
const API = process.env.API_BASE_URL ?? "http://localhost:8000";

export class BackendError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function callBackend(
  token: string,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<unknown> {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (resp.status === 204) return null;

  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;

  if (!resp.ok) {
    const detail = (data as { detail?: string } | null)?.detail ?? resp.statusText;
    throw new BackendError(resp.status, detail);
  }
  return data;
}

export function toolText(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function toolError(err: unknown) {
  const message = err instanceof BackendError ? `(${err.status}) ${err.message}` : String(err);
  return { content: [{ type: "text" as const, text: message }], isError: true };
}
