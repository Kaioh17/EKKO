const BASE_URL = "http://localhost:8000";
export const API_TOKEN = import.meta.env.VITE_EKKO_API_TOKEN ?? "";

export interface ShortMemory {
  prior_question: string;
  prior_answer: string;
  follow_up: string;
  follow_up_answer: string | null;
  timestamp: string;
  status: string;
}

export interface Slot {
  name: string;
  type: string;
  values: string[];
  triggers: string[];
}

export interface Intent {
  key: string;
  examples: string[];
  handler: string;
  slots: Slot[];
}

export interface Domain {
  key: string;
  threshold: number;
  continuity_boost: number;
}

export interface MemoryLine {
  date: string;
  text: string;
}

export interface LongTermMemory {
  sections: Record<string, MemoryLine[]>;
}

// FastAPI error body -> one readable line (422 lists, 409 {message}, plain strings).
function errorDetail(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d: { loc?: unknown[]; msg?: string }) => `${d.loc?.[d.loc.length - 1] ?? "?"}: ${d.msg}`).join("; ");
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...init?.headers, "X-Ekko-Token": API_TOKEN },
  });
  if (!res.ok) {
    const detail = errorDetail(await res.json().catch(() => null));
    throw new Error(detail ?? `${init?.method ?? "GET"} ${path} -> ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const getShortTermMemory = () => request<ShortMemory | null>("/api/memory/short-term");
export const clearShortTermMemory = () =>
  request<void>("/api/memory/short-term/clear", { method: "POST" });
export const getLongTermMemory = () => request<LongTermMemory>("/api/memory/long-term");

export const getIntents = () => request<Intent[]>("/api/routing/intents");
export const getThreshold = () => request<{ threshold: number }>("/api/routing/threshold");
export const getDomains = () => request<Domain[]>("/api/routing/domains");

export interface ChatReply {
  reply: string;
  model: string | null;
}

export const sendChat = (message: string) =>
  request<ChatReply>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

export const sendWake = () => request<{ sent: string }>("/api/wake", { method: "POST" });
export const restartListener = () => request<{ started: boolean }>("/api/restart", { method: "POST" });

export interface GeneralSettings {
  vad_threshold: number;
  wake_word: string;
  wake_threshold: number;
  verify_threshold: number | null; // null = auto
  command_verify_threshold: number;
  routing_threshold: number;
  min_silence_ms: number;
  active_window_s: number;
  feedback_tail_ms: number;
  whisper_model: "tiny" | "base" | "small" | "medium" | "large-v3";
  no_save: boolean;
  no_verify: boolean;
  no_transcribe: boolean;
  no_execute: boolean;
  no_llm_fallback: boolean;
  no_manual_wake: boolean;
  no_hard_stop: boolean;
}

// Every settings section (backend/routers/settings.py SECTIONS) shares these routes.
export const getSettings = <T,>(section: string) => request<T>(`/api/settings/${section}`);
export const patchSettings = <T,>(section: string, changes: Partial<T>) =>
  request<T>(`/api/settings/${section}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
export const resetSettings = <T,>(section: string) =>
  request<T>(`/api/settings/${section}/reset`, { method: "POST" });

export interface LlmUsage {
  provider: string;
  calls: number;
  avg_latency_s: number | null;
  tokens: number;
  cached_tokens: number;
  cost_usd: number | null;
}

export interface GeneralOverview {
  llm_total: LlmUsage;
  llm_providers: LlmUsage[];
  routing: {
    recent_scores: number[];
    match_rate: number | null;
    threshold: number;
    last_status: "matched" | "missing_slot" | "no_match" | null;
    last_intent: string | null;
  };
  memory: { accepted: number; rejected: number; pending: number };
  hotkeys: { name: string; chord: string; enabled: boolean }[];
  ekko_os: string | null;
  gemini_key_hint: string | null;
}

export const getGeneralOverview = () => request<GeneralOverview>("/api/overview/general");
