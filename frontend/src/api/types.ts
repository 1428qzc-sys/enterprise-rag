export interface KnowledgeBase {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dim: number;
  vector_backend: string;
  vector_collection: string;
  vector_revision: number;
  reindex_status: string;
  reindex_progress: number;
  reindex_error: string;
  consistency_status: string;
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
  role_ids: string[];
  role_names: string[];
}

export interface PermissionInfo {
  code: string;
  description: string;
}

export interface RoleInfo {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  permissions: string[];
  user_count: number;
  is_system: boolean;
  created_at: string;
}

export interface AuditLogItem {
  id: string;
  tenant_id: string;
  user_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  ip_address: string;
  user_agent: string;
  detail: Record<string, unknown>;
  created_at: string;
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
  status: "pending" | "processing" | "done" | "failed" | "canceled";
  error: string;
  chunk_count: number;
  active_version_id: string;
  version: number;
  latest_version: number;
  content_hash: string;
  progress: number;
  retry_count: number;
  cancel_requested: boolean;
  consistency_status: string;
  cleanup_error: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentVersionItem {
  id: string;
  document_id: string;
  version_number: number;
  name: string;
  source_type: string;
  source: string;
  mime: string;
  size_bytes: number;
  content_hash: string;
  status: DocumentItem["status"];
  error: string;
  progress: number;
  chunk_count: number;
  embedding_provider: string;
  embedding_model: string;
  embedding_dim: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface IngestionJobItem {
  id: string;
  document_id: string;
  version_id: string;
  kind: "ingest" | "reembed" | "reconcile" | string;
  status: "pending" | "processing" | "done" | "failed" | "canceled";
  stage: string;
  progress: number;
  attempt: number;
  max_attempts: number;
  cancel_requested: boolean;
  error: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentChunk {
  id: string;
  document_id: string;
  version_id: string;
  chunk_index: number;
  content: string;
  char_count: number;
  page: number | null;
  meta: Record<string, unknown>;
  is_active: boolean;
  injection_risk: boolean;
}

export interface SourceChunk {
  index: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  chunk_index: number;
  page: number | null;
  score: number;
  score_type: "rrf" | "rerank" | string;
  vector_score: number | null;
  bm25_score: number | null;
  rrf_score: number;
  rerank_score: number | null;
  injection_risk: boolean;
  content: string;
}

export interface RetrievalStageDiagnostics {
  status: string;
  elapsed_ms: number;
  candidate_count: number;
  error?: string;
  provider?: string;
  method?: string;
}

export interface RetrievalDiagnostics {
  cached?: boolean;
  total_ms?: number;
  result_count?: number;
  degraded?: boolean;
  degraded_reasons?: string[];
  generation_context_count?: number;
  injection_risk_excluded?: number;
  vector?: RetrievalStageDiagnostics;
  bm25?: RetrievalStageDiagnostics;
  fusion?: RetrievalStageDiagnostics;
  rerank?: RetrievalStageDiagnostics;
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
  request_id?: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceChunk[];
  created_at?: string;
  streaming?: boolean;
  diagnostics?: RetrievalDiagnostics;
  error?: string;
  retryable?: boolean;
}
