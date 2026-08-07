import axios from "axios";
import type {
  AuditLogItem,
  AuthResponse,
  AuthUser,
  ChatMessage,
  Conversation,
  DocumentChunk,
  DocumentItem,
  DocumentVersionItem,
  IngestionJobItem,
  KnowledgeBase,
  MeResponse,
  PermissionInfo,
  RoleInfo,
  RetrievalDiagnostics,
  SourceChunk,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TOKEN_KEY = "enterprise-rag.access-token";
export const AUTH_EXPIRED_EVENT = "enterprise-rag:auth-expired";

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

export function apiErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data
    ?.detail;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

function notifyAuthExpired(): void {
  if (!getAuthToken()) return;
  clearAuthToken();
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

http.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) notifyAuthExpired();
    return Promise.reject(error);
  },
);

export const authApi = {
  login: (tenantSlug: string, email: string, password: string) =>
    http
      .post<AuthResponse>("/api/auth/login", {
        tenant_slug: tenantSlug,
        email,
        password,
      })
      .then((r) => r.data),
  me: () => http.get<MeResponse>("/api/auth/me").then((r) => r.data),
  logout: () => clearAuthToken(),
};

// ==================== 租户管理 ====================
export const adminApi = {
  permissions: () =>
    http.get<PermissionInfo[]>("/api/admin/permissions").then((r) => r.data),
  roles: () => http.get<RoleInfo[]>("/api/admin/roles").then((r) => r.data),
  createRole: (body: { name: string; description: string; permissions: string[] }) =>
    http.post<RoleInfo>("/api/admin/roles", body).then((r) => r.data),
  updateRole: (
    id: string,
    body: Partial<Pick<RoleInfo, "name" | "description" | "permissions">>,
  ) => http.patch<RoleInfo>(`/api/admin/roles/${id}`, body).then((r) => r.data),
  removeRole: (id: string) => http.delete(`/api/admin/roles/${id}`),
  users: () => http.get<AuthUser[]>("/api/admin/users").then((r) => r.data),
  createUser: (body: {
    email: string;
    password: string;
    display_name: string;
    is_active: boolean;
    role_ids: string[];
  }) => http.post<AuthUser>("/api/admin/users", body).then((r) => r.data),
  updateUser: (
    id: string,
    body: Partial<{
      display_name: string;
      password: string;
      is_active: boolean;
      role_ids: string[];
    }>,
  ) => http.patch<AuthUser>(`/api/admin/users/${id}`, body).then((r) => r.data),
  auditLogs: (params?: { action?: string; outcome?: string; offset?: number; limit?: number }) =>
    http.get<AuditLogItem[]>("/api/admin/audit-logs", { params }).then((r) => r.data),
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
  get: (kbId: string, docId: string) =>
    http
      .get<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/${docId}`)
      .then((r) => r.data),
  upload: (kbId: string, file: File, onProgress?: (percent: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    return http
      .post<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/upload`, form, {
        onUploadProgress: (event) => {
          if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100));
        },
      })
      .then((r) => r.data);
  },
  uploadVersion: (
    kbId: string,
    docId: string,
    file: File,
    onProgress?: (percent: number) => void,
  ) => {
    const form = new FormData();
    form.append("file", file);
    return http
      .post<DocumentItem>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/versions/upload`,
        form,
        {
          onUploadProgress: (event) => {
            if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100));
          },
        },
      )
      .then((r) => r.data);
  },
  ingestUrl: (kbId: string, url: string) =>
    http
      .post<DocumentItem>(`/api/knowledge-bases/${kbId}/documents/url`, { url })
      .then((r) => r.data),
  ingestUrlVersion: (kbId: string, docId: string, url: string) =>
    http
      .post<DocumentItem>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/versions/url`,
        { url },
      )
      .then((r) => r.data),
  versions: (kbId: string, docId: string) =>
    http
      .get<DocumentVersionItem[]>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/versions`,
      )
      .then((r) => r.data),
  jobs: (kbId: string, docId: string) =>
    http
      .get<IngestionJobItem[]>(`/api/knowledge-bases/${kbId}/documents/${docId}/jobs`)
      .then((r) => r.data),
  chunks: (kbId: string, docId: string, versionId?: string) =>
    http
      .get<DocumentChunk[]>(`/api/knowledge-bases/${kbId}/documents/${docId}/chunks`, {
        params: versionId ? { version_id: versionId } : undefined,
      })
      .then((r) => r.data),
  cancelJob: (kbId: string, docId: string, jobId: string) =>
    http
      .post<IngestionJobItem>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/jobs/${jobId}/cancel`,
      )
      .then((r) => r.data),
  retryJob: (kbId: string, docId: string, jobId: string) =>
    http
      .post<IngestionJobItem>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/jobs/${jobId}/retry`,
      )
      .then((r) => r.data),
  reconcile: (kbId: string, docId: string) =>
    http
      .post<IngestionJobItem>(
        `/api/knowledge-bases/${kbId}/documents/${docId}/reconcile`,
      )
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
  exportMarkdown: (conversationId: string) =>
    http
      .get<Blob>(`/api/conversations/${conversationId}/export.md`, { responseType: "blob" })
      .then((response) => response.data),
  retrieve: (kbId: string, query: string, topK?: number) =>
    http
      .post<{ query: string; results: SourceChunk[]; diagnostics: RetrievalDiagnostics }>(
        "/api/retrieve",
        {
        kb_id: kbId,
        query,
        top_k: topK,
        },
      )
      .then((r) => r.data),
};

// ==================== 流式问答（SSE over fetch） ====================
export interface StreamHandlers {
  onMeta?: (data: {
    conversation_id: string;
    request_id: string;
    used_query: string;
    diagnostics: RetrievalDiagnostics;
  }) => void;
  onSources?: (data: SourceChunk[]) => void;
  onToken?: (text: string) => void;
  onReplace?: (data: { answer: string; sources: SourceChunk[] }) => void;
  onDone?: (data: {
    message_id: string;
    conversation_id: string;
    request_id: string;
    answer: string;
    sources: SourceChunk[];
    diagnostics: RetrievalDiagnostics;
  }) => void;
  onError?: (message: string) => void;
}

type StreamMetaPayload = Parameters<NonNullable<StreamHandlers["onMeta"]>>[0];
type StreamSourcesPayload = Parameters<NonNullable<StreamHandlers["onSources"]>>[0];
type StreamReplacePayload = Parameters<NonNullable<StreamHandlers["onReplace"]>>[0];
type StreamDonePayload = Parameters<NonNullable<StreamHandlers["onDone"]>>[0];

export class StreamChatError extends Error {
  constructor(
    message: string,
    readonly status = 0,
    readonly retryable = true,
  ) {
    super(message);
    this.name = "StreamChatError";
  }
}

export function createRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

export async function streamChat(
  body: {
    kb_id: string;
    question: string;
    conversation_id?: string | null;
    request_id: string;
    top_k?: number;
  },
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
    let detail = `请求失败：HTTP ${resp.status}`;
    try {
      const payload = await resp.json();
      detail = payload?.detail || detail;
    } catch {
      /* 响应不是 JSON */
    }
    if (resp.status === 401) notifyAuthExpired();
    throw new StreamChatError(detail, resp.status, resp.status >= 500 || resp.status === 429);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let completed = false;
  let serverError = "";

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
    let data: unknown = dataStr;
    try {
      data = JSON.parse(dataStr);
    } catch {
      /* keep string */
    }
    const dataRecord =
      typeof data === "object" && data !== null ? (data as Record<string, unknown>) : {};
    switch (event) {
      case "meta":
        handlers.onMeta?.(data as StreamMetaPayload);
        break;
      case "sources":
        handlers.onSources?.(data as StreamSourcesPayload);
        break;
      case "token":
        handlers.onToken?.(typeof dataRecord.text === "string" ? dataRecord.text : "");
        break;
      case "replace":
        handlers.onReplace?.(data as StreamReplacePayload);
        break;
      case "done":
        completed = true;
        handlers.onDone?.(data as StreamDonePayload);
        break;
      case "error":
        serverError =
          typeof dataRecord.message === "string" ? dataRecord.message : "未知错误";
        handlers.onError?.(serverError);
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
  if (serverError) throw new StreamChatError(serverError, 0, true);
  if (!completed) throw new StreamChatError("流式连接提前中断", 0, true);
}
