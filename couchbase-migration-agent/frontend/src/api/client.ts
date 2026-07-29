/** Thin typed fetch wrapper for the FastAPI backend. */
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
export const WS_BASE = import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch (e: any) {
    // fetch() throws (rather than resolving with a non-ok Response) for network-level
    // failures: the backend isn't reachable at this URL, a CORS preflight was rejected,
    // DNS failed, etc. Browsers report all of these with the same generic, unhelpful
    // message ("NetworkError when attempting to fetch resource." in Firefox, "Failed to
    // fetch" in Chrome) -- surface the URL we were trying to reach instead, since that's
    // almost always enough to tell "backend isn't running" from "wrong API base URL" from
    // "CORS: this origin isn't in the backend's CORS_ORIGINS".
    throw new Error(
      `Could not reach ${url} (${e?.message || e}). Check that the backend container is ` +
      `running (docker compose ps / docker compose logs backend), and that VITE_API_BASE_URL ` +
      `(currently "${API_BASE}") is correct for how you're loading this page. If you're ` +
      `loading the UI from something other than localhost, the backend's CORS_ORIGINS also ` +
      `needs to include that origin.`
    );
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// --- domain-specific calls -------------------------------------------------

export const testConnection = (config: unknown) => api.post<any>("/api/clusters/test-connection", config);

export const createMigration = (plan: unknown) => api.post<any>("/api/migrations", plan);
export const listMigrations = () => api.get<any[]>("/api/migrations");
export const getMigration = (id: string) => api.get<any>(`/api/migrations/${id}`);
export const validateMigration = (id: string) => api.post<any>(`/api/migrations/${id}/validate`);
export const backupMigration = (id: string) => api.post<any>(`/api/migrations/${id}/backup`);
export const approveMigration = (id: string, approvedBy: string) =>
  api.post<any>(`/api/migrations/${id}/approve`, { migration_id: id, approved_by: approvedBy });
export const startMigration = (id: string) => api.post<any>(`/api/migrations/${id}/start`);
export const rollbackMigration = (id: string, reason: string) =>
  api.post<any>("/api/backup/rollback", { migration_id: id, reason });
export const stopReplication = (id: string, performCutover: boolean) =>
  api.post<any>(`/api/migrations/${id}/replication/stop`, { migration_id: id, perform_cutover: performCutover });

export const chatWithAgent = (message: string, migrationId?: string) =>
  api.post<any>("/api/agent/chat", { message, migration_id: migrationId, use_memory: true });

export const recommendReplicationMode = (
  cutoverPlan: "cutover" | "phased",
  sourceTopology: unknown,
  parallelism: number,
) =>
  api.post<any>("/api/agent/recommend-replication-mode", {
    cutover_plan: cutoverPlan,
    source_topology: sourceTopology,
    parallelism,
  });

// Not a JSON call -- this is a direct download URL for an <a href download> link,
// so the browser handles the file transfer itself rather than this app buffering
// the whole (potentially multi-GB) zip through a fetch()/blob round-trip.
export const backupDownloadUrl = (migrationId: string) => `${API_BASE}/api/backup/${migrationId}/download`;
