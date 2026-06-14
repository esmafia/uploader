import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

export interface Account {
  id: number;
  username: string;
  display_name: string | null;
  cookie_path: string;
  has_valid_session: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface Schedule {
  id: number;
  account_id: number;
  source_type: "local" | "youtube";
  source_ref: string;
  title: string;
  options_json: string;
  scheduled_for: string;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  result_text: string | null;
  attempts: number;
  heartbeat_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface VideoFile {
  name: string;
  size_bytes: number;
  modified_at: string;
}

export const Accounts = {
  list: () => api.get<Account[]>("/accounts").then(r => r.data),
  create: (username: string, display_name?: string) =>
    api.post<Account>("/accounts", { username, display_name }).then(r => r.data),
  remove: (id: number) => api.delete(`/accounts/${id}`),
  importFromDisk: () => api.post<Account[]>("/accounts/import-from-disk").then(r => r.data),
  update: (id: number, display_name: string) =>
    api.patch<Account>(`/accounts/${id}`, { display_name }).then(r => r.data),
};

export const Schedules = {
  list: () => api.get<Schedule[]>("/schedules").then(r => r.data),
  create: (payload: unknown) => api.post<Schedule>("/schedules", payload).then(r => r.data),
  cancel: (id: number) =>
    api.patch<Schedule>(`/schedules/${id}`, { status: "cancelled" }).then(r => r.data),
  remove: (id: number) => api.delete(`/schedules/${id}`),
  retry: (id: number) =>
    api.patch<Schedule>(`/schedules/${id}`, { status: "pending" }).then(r => r.data),
};

export const Videos = {
  list: () => api.get<VideoFile[]>("/videos").then(r => r.data),
};

export const Uploads = {
  file: (fd: FormData) =>
    api.post<{ ok: boolean; message: string }>("/uploads/file", fd, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then(r => r.data),
  youtube: (payload: unknown) =>
    api.post<{ ok: boolean; message: string }>("/uploads/youtube", payload).then(r => r.data),
};

export const Login = {
  start: (username: string) =>
    api.post<{ session_id: string; vnc_url: string }>("/login/browser/start", { username }).then(r => r.data),
  get: (id: string) => api.get(`/login/browser/${id}`).then(r => r.data),
  cancel: (id: string) => api.delete(`/login/browser/${id}`),
  eventStreamUrl: (id: string) => `/api/login/browser/${id}/events`,
};
