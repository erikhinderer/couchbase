/** Thin typed fetch wrapper for the FastAPI backend. Requests are made relative
 * to the current origin by default -- in production the frontend's nginx
 * container reverse-proxies /api and /ws to the backend service so everything
 * is same-origin over the single HTTPS port 443 (see frontend/nginx.conf). Set
 * VITE_API_BASE_URL / VITE_WS_BASE_URL to override for local `npm run dev`. */
const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
export const WS_BASE =
  import.meta.env.VITE_WS_BASE_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`
    : "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
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
};

// --- domain-specific calls -------------------------------------------------

export const testConnection = (config: unknown) => api.post<any>("/api/clusters/test-connection", config);

export const listModels = () => api.get<any[]>("/api/models");

export const createJob = (plan: unknown) => api.post<any>("/api/jobs", plan);
export const listJobs = () => api.get<any[]>("/api/jobs");
export const getJob = (id: string) => api.get<any>(`/api/jobs/${id}`);
export const validateJob = (id: string) => api.post<any>(`/api/jobs/${id}/validate`);
export const launchJob = (id: string) => api.post<any>(`/api/jobs/${id}/launch`);
export const stopJob = (id: string) => api.post<any>(`/api/jobs/${id}/stop`);

export const getDashboardStats = () => api.get<any>("/api/stats/dashboard");

export const chatWithAgent = (message: string, jobId?: string) =>
  api.post<any>("/api/agent/chat", { message, job_id: jobId });
