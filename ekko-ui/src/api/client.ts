import { backend } from "./backend";

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
  const { url, token } = await backend();
  const res = await fetch(`${url}${path}`, {
    ...init,
    headers: { ...init?.headers, "X-Ekko-Token": token },
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

const JSON_HEADERS = { "Content-Type": "application/json" };

// Long-term lines are addressed by section + index; `expected` is the text the
// UI saw there, so an edit made elsewhere in the meantime is a 409, not a wrong write.
export const addLongTermLine = (section: string, text: string) =>
  request<LongTermMemory>("/api/memory/long-term/lines", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ section, text }),
  });
export const editLongTermLine = (section: string, index: number, expected: string, text: string) =>
  request<LongTermMemory>("/api/memory/long-term/lines", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ section, index, expected, text }),
  });
export const deleteLongTermLine = (section: string, index: number, expected: string) =>
  request<LongTermMemory>(
    `/api/memory/long-term/lines?${new URLSearchParams({ section, index: String(index), expected })}`,
    { method: "DELETE" },
  );

export interface DayTurn {
  ts: string;
  source: string;
  kind: string;
  user: string;
  assistant: string;
}

export interface DayLog {
  turns: DayTurn[];
  window_hours: number;
  token_budget: number;
  tokens_est: number;
  truncated_turns: number;
}

export const getDayLog = () => request<DayLog>("/api/memory/day-log");
export const deleteDayTurn = (ts: string) =>
  request<void>(`/api/memory/day-log/turn?${new URLSearchParams({ ts })}`, { method: "DELETE" });
export const clearDayLog = () => request<void>("/api/memory/day-log", { method: "DELETE" });

export interface ArchiveEntry {
  key: string;
  date: string;
  summary: string;
  topics: string[];
  turns: number;
  details: string;
}

export interface Archive {
  entries: ArchiveEntry[];
  keys_sent: number;
  key_cap: number;
}

export const getArchive = () => request<Archive>("/api/memory/archive");
export const deleteArchiveEntry = (key: string) =>
  request<void>(`/api/memory/archive?${new URLSearchParams({ key })}`, { method: "DELETE" });

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
  command_min_silence_ms: number;
  active_window_s: number;
  feedback_tail_ms: number;
  whisper_model: "auto" | "tiny" | "base" | "small" | "medium" | "large-v3";
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
}

// Provider names come from GET /api/llm/providers (llm_fallback/catalog.py); the UI keeps no list of them.
export type Provider = string;

export interface ProviderInfo {
  name: Provider;
  label: string;
  kind: string;
  detail: string;
  key_env: KeyName | null;
  paid: boolean;
  model_setting: "openai_model" | "claude_api_model" | null;
  model: string | null;
}

export const getProviders = () => request<ProviderInfo[]>("/api/llm/providers");

export interface LlmSettings {
  provider: Provider;
  failover_enabled: boolean;
  failover_order: Provider[];
  failover_cooldown_s: number;
  research_enabled: boolean;
  openai_model: string;
  claude_api_model: string;
  budget_usd: Record<Provider, number>;
}

export interface SystemSettings {
  os: "auto" | "windows" | "linux" | "mac";
}

export interface VoiceSettings {
  tts_engine: "piper" | "openai" | "kokoro" | "chattts";
  tts_streaming: boolean;
  openai_tts_model: string;
  openai_tts_voice: string;
  openai_tts_instructions: string;
  kokoro_voice: string;
  chattts_seed: number;
}

export type KeyName = string; // an env var name; GET /api/keys lists the valid ones
export type KeyStatus = Record<KeyName, { set: boolean; last4: string | null }>;

export const getKeys = () => request<KeyStatus>("/api/keys");
export const setKey = (name: KeyName, value: string) =>
  request<void>(`/api/keys/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
export const deleteKey = (name: KeyName) => request<void>(`/api/keys/${name}`, { method: "DELETE" });

export const getGeneralOverview = () => request<GeneralOverview>("/api/overview/general");

export interface ModelsStatus {
  phase: "idle" | "downloading" | "ready" | "failed";
  current: string | null;
  error: string | null;
  missing: string[];
}

export const getModels = () => request<ModelsStatus>("/api/models");

export interface Autostart {
  supported: boolean;
  enabled: boolean;
}

export const getAutostart = () => request<Autostart>("/api/autostart");
export const setAutostart = (enabled: boolean) =>
  request<Autostart>("/api/autostart", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
