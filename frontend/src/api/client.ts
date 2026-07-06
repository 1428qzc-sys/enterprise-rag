import axios from "axios";
import type {
  AuthResponse,
  ChatMessage,
  Conversation,
  DocumentItem,
  KnowledgeBase,
  MeResponse,
  SourceChunk,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TOKEN_KEY = "enterprise-rag.access-token";

export const http = axios.create({ baseURL: API_BASE, timeout: 60000 });

export function getAuthToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

http.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export const authApi = {
  login: (email: string, password: string) =>
    http.post<AuthResponse>("/api/auth/login", { email, password }).then((r) => r.data),
  me: () => http.get<MeResponse>("/api/auth/me").then((r) => r.data),
  logout: () => clearAuthToken(),
};

// ==================== 知识库 ====================
export const kbApi = {
  list: () => http.get<KnowledgeBase[]>("/api/knowledge-bases").then((r) => r.data),
  create: (name: string, description = "") =>
    http.post<KnowledgeBase>("/api/knowledge-bases", { name, description }).then((r) => r.data),
  get: (id: string) => http.get<KnowledgeBase>(`/api/knowledge-bases/${id}`).then((r) => r.data),
  update: (id: string, body: Partial<Pick<KnowledgeBase, "name" | "description">>) =>
    http.patch<KnowledgeBase>(`/api/knowledge-bases/${id}`, body).then((r) => r.data),
  remove: (id: string) => http.delete(`/api/knowledge-bases/${id}`),
};

// ==================== 文档 ====================
export const docApi = {
  list: (kbId: string) =>
    http.get<DocumentItem[]>(`/api/knowledge-bases/${kbId}/documents`).then((r) => r.data),
  upload: (kbId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http
      .post<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/upload`, form)
      .then((r) => r.data);
  },
  ingestUrl: (kbId: string, url: string) =>
    http
      .post<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/url`, { url })
      .then((r) => r.data),
  remove: (kbId: string, docId: string) =>
    http.delete(`/api/knowledge-bases/${kbId}/documents/${docId}`),
  reembed: (kbId: string, docId: string) =>
    http
      .post<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/${docId}/reembed`)
      .then((r) => r.data),
};

// ==================== 会话 ====================
export const chatApi = {
  conversations: (kbId: string) =>
    http.get<Conversation[]>(`/api/knowledge-bases/${kbId}/conversations`).then((r) => r.data),
  messages: (conversationId: string) =>
    http.get<ChatMessage[]>(`/api/conversations/${conversationId}/messages`).then((r) => r.data),
  removeConversation: (conversationId: string) =>
    http.delete(`/api/conversations/${conversationId}`),
  retrieve: (kbId: string, query: string, topK?: number) =>
    http
      .post<{ query: string; results: SourceChunk[] }>("/api/retrieve", {
        kb_id: kbId,
        query,
        top_k: topK,
      })
      .then((r) => r.data),
};

// ==================== 流式问答（SSE over fetch） ====================
export interface StreamHandlers {
  onMeta?: (data: { conversation_id: string; used_query: string }) => void;
  onSources?: (data: SourceChunk[]) => void;
  onToken?: (text: string) => void;
  onDone?: (data: { message_id: string; conversation_id: string }) => void;
  onError?: (message: string) => void;
}

export async function streamChat(
  body: { kb_id: string; question: string; conversation_id?: string | null; top_k?: number },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}),
    },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    handlers.onError?.(`请求失败：HTTP ${resp.status}`);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatch = (raw: string) => {
    const block = raw.replace(/\r\n/g, "\n").trim();
    if (!block) return;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    const dataStr = dataLines.join("\n");
    let data: any = dataStr;
    try {
      data = JSON.parse(dataStr);
    } catch {
      /* keep string */
    }
    switch (event) {
      case "meta":
        handlers.onMeta?.(data);
        break;
      case "sources":
        handlers.onSources?.(data);
        break;
      case "token":
        handlers.onToken?.(data.text ?? "");
        break;
      case "done":
        handlers.onDone?.(data);
        break;
      case "error":
        handlers.onError?.(data.message ?? "未知错误");
        break;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    // 事件以空行分隔
    while ((idx = buffer.indexOf("\n\n")) !== -1 || (idx = buffer.indexOf("\r\n\r\n")) !== -1) {
      const sep = buffer.slice(idx, idx + 4) === "\r\n\r\n" ? 4 : 2;
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + sep);
      dispatch(chunk);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}
