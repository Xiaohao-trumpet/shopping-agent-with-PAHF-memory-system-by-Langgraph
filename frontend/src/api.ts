import type { MemoryItem, MemorySearchHit, ModelInfo } from "./types";

const DEFAULT_API_BASE = import.meta.env.PROD ? "/server" : "http://localhost:8000";
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE).replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

export async function fetchHealth(): Promise<{ status: string; model_name: string }> {
  return request("/health");
}

export async function fetchModels(): Promise<ModelInfo[]> {
  try {
    const res = await request<{ data: ModelInfo[] }>("/api/v1/models");
    return res.data ?? [];
  } catch {
    const res = await request<{ data: ModelInfo[] }>("/v1/models");
    return res.data ?? [];
  }
}

export async function fetchPromptScenes(): Promise<{ scenes: string[]; default_scene: string }> {
  return request("/api/v1/prompt-scenes");
}

export async function sendChatCompletion(payload: {
  model: string;
  userId: string;
  systemPrompt?: string;
  scene?: string;
  messages: { role: "user" | "assistant"; content: string }[];
}): Promise<{ content: string; trace: Record<string, unknown> | null }> {
  const body = {
    model: payload.model,
    user: payload.userId,
    stream: false,
    messages: [
      ...(payload.systemPrompt
        ? [
            {
              role: "system",
              content: `${payload.systemPrompt}${
                payload.scene ? `\n\nSelected scene: ${payload.scene}` : ""
              }`,
            },
          ]
        : payload.scene
        ? [{ role: "system", content: `Selected scene: ${payload.scene}` }]
        : []),
      ...payload.messages,
    ],
  };

  const response = await request<{
    choices: Array<{ message: { content: string } }>;
    trace?: Record<string, unknown>;
  }>("/api/v1/chat/completions", {
    method: "POST",
    body: JSON.stringify(body),
  });

  return {
    content: response.choices?.[0]?.message?.content ?? "",
    trace: response.trace ?? null,
  };
}

export async function listMemories(userId: string): Promise<MemoryItem[]> {
  return request(`/api/v1/memory?user_id=${encodeURIComponent(userId)}`);
}

export async function addMemory(userId: string, text: string): Promise<MemoryItem> {
  return request("/api/v1/memory", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      text,
    }),
  });
}

export async function updateMemory(
  memoryId: number,
  userId: string,
  text: string
): Promise<MemoryItem> {
  return request(`/api/v1/memory/${encodeURIComponent(String(memoryId))}`, {
    method: "PUT",
    body: JSON.stringify({
      user_id: userId,
      text,
    }),
  });
}

export async function searchMemories(
  userId: string,
  query: string,
  topK = 5
): Promise<MemorySearchHit[]> {
  const res = await request<{ hits: MemorySearchHit[] }>("/api/v1/memory/search", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      query,
      top_k: topK,
    }),
  });
  return res.hits ?? [];
}

export async function findSimilarMemory(
  userId: string,
  text: string,
  threshold?: number
): Promise<MemoryItem | null> {
  return request<MemoryItem | null>("/api/v1/memory/find-similar", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      text,
      threshold,
    }),
  });
}
