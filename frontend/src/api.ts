// Tiny API client. Every call goes to /api/... (nginx or the Vite dev server forwards it to FastAPI).

const BASE = import.meta.env.VITE_API_URL ?? "/api";
const TOKEN_KEY = "actionpilot.token";

export type Status =
  | "queued"
  | "transcribing"
  | "analyzing"
  | "retrying"
  | "awaiting_approval"
  | "sending"
  | "finishing"
  | "completed"
  | "failed";

export const IN_PROGRESS: Status[] = ["queued", "transcribing", "analyzing", "retrying", "sending", "finishing"];

export interface MeetingRow {
  id: string;
  title: string;
  meeting_date: string;
  status: Status;
  source: string;
  error: string | null;
  created_at: string;
  processing_ms: number | null;
  task_count: number;
  email_status: string | null;
}

export interface Task {
  id: number;
  meeting_id?: string | null;
  meeting_title?: string | null;
  title: string;
  assignee: string | null;
  due_date: string | null;
  priority: "low" | "medium" | "high";
  status: "open" | "done";
  remind_at: string | null;
  reminder_status: string | null;
  overdue?: boolean;
}

export interface Email {
  id: number;
  recipients: string[];
  blocked_recipients: string[];
  subject: string;
  body: string;
  status: "draft" | "approved" | "sent" | "rejected" | "failed";
  error: string | null;
  sent_at: string | null;
}

export interface Step {
  step: string;
  detail: Record<string, unknown>;
  ms: number | null;
  created_at: string;
}

export interface Meeting extends Omit<MeetingRow, "task_count" | "email_status"> {
  participants: string[];
  transcript: string | null;
  summary: string | null;
  decisions: string[];
  attempts: number;
  run_no: number;
  filename: string | null;
  usage: { prompt_tokens?: number; completion_tokens?: number; llm_calls?: number; cost_usd?: number };
  tasks: Task[];
  email: Email | null;
  steps: Step[];
}

export interface SearchHit {
  meeting_id: string;
  title: string;
  meeting_date: string;
  kind: string;
  content: string;
  score: number;
}

export interface Stats {
  meetings: {
    meetings: number;
    completed: number;
    awaiting_approval: number;
    failed: number;
    avg_processing_ms: number | null;
    p95_processing_ms: number | null;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
  tasks: { total: number; open: number; done: number; overdue: number };
  emails: { sent: number; rejected: number; with_blocked_recipients: number };
  reminders: { scheduled: number; sent: number };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const auth = {
  get token() {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set token(value: string | null) {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* private mode: the session just won't survive a reload */
    }
  },
};

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401 && auth.token) {
    auth.token = null;
    onUnauthorized();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, errorMessage(body, `${res.status} ${res.statusText}`));
  }
  return (res.status === 204 ? null : await res.json()) as T;
}

export const api = {
  register: (email: string, password: string) =>
    request("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: async (email: string, password: string) => {
    const form = new URLSearchParams({ username: email, password });
    const r = await request<{ access_token: string }>("/auth/login", { method: "POST", body: form });
    auth.token = r.access_token;
  },
  me: () => request<{ id: string; email: string }>("/auth/me"),

  meetings: () => request<MeetingRow[]>("/meetings"),
  meeting: (id: string) => request<Meeting>(`/meetings/${id}`),
  createMeeting: (form: FormData) => request<MeetingRow>("/meetings", { method: "POST", body: form }),
  approve: (id: string, body: { action: "approve" | "reject"; to?: string[]; subject?: string; body?: string }) =>
    request(`/meetings/${id}/approval`, { method: "POST", body: JSON.stringify(body) }),
  retry: (id: string) => request(`/meetings/${id}/retry`, { method: "POST" }),
  reprocess: (id: string) => request(`/meetings/${id}/reprocess`, { method: "POST" }),
  deleteMeeting: (id: string) => request(`/meetings/${id}`, { method: "DELETE" }),

  tasks: (status: "open" | "done" | "all") => request<Task[]>(`/tasks?status=${status}`),
  createTask: (body: { title: string; assignee?: string; due_date?: string; priority?: string }) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify(body) }),
  updateTask: (id: number, body: Partial<Pick<Task, "status" | "title" | "due_date" | "priority" | "assignee">>) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteTask: (id: number) => request(`/tasks/${id}`, { method: "DELETE" }),

  search: (q: string) => request<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
  stats: () => request<Stats>("/stats"),
};
