export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dim: number;
  vector_backend: string;
  document_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface TenantInfo {
  id: string;
  name: string;
  slug: string;
}

export interface AuthUser {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  is_superuser: boolean;
  permissions: string[];
}

export interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
  tenant: TenantInfo;
}

export interface MeResponse {
  user: AuthUser;
  tenant: TenantInfo;
}

export interface DocumentItem {
  id: string;
  kb_id: string;
  name: string;
  source_type: string;
  source: string;
  mime: string;
  size_bytes: number;
  status: "pending" | "processing" | "done" | "failed";
  error: string;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface SourceChunk {
  index: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  chunk_index: number;
  page: number | null;
  score: number;
  content: string;
}

export interface Conversation {
  id: string;
  kb_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceChunk[];
  created_at?: string;
  streaming?: boolean;
}
