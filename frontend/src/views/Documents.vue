<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleX,
  Eye,
  FileText,
  Globe2,
  History,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Trash2,
  UploadCloud,
  Wrench,
} from "@lucide/vue";
import { docApi } from "@/api/client";
import type {
  DocumentChunk,
  DocumentItem,
  DocumentVersionItem,
  IngestionJobItem,
} from "@/api/types";
import KbTabs from "@/components/KbTabs.vue";
import { useToast } from "@/composables/toast";
import { useAuthStore } from "@/stores/auth";
import { useKbStore } from "@/stores/kb";

type DetailTab = "versions" | "jobs" | "source";

interface DetailState {
  loading: boolean;
  chunksLoading: boolean;
  error: string;
  tab: DetailTab;
  versions: DocumentVersionItem[];
  jobs: IngestionJobItem[];
  chunks: DocumentChunk[];
  selectedVersionId: string;
}

const route = useRoute();
const kbId = route.params.id as string;
const { show } = useToast();
const auth = useAuthStore();
const store = useKbStore();

const docs = ref<DocumentItem[]>([]);
const details = ref<Record<string, DetailState>>({});
const dragging = ref(false);
const url = ref("");
const versionUrls = ref<Record<string, string>>({});
const busy = ref(false);
const loading = ref(true);
const loadError = ref("");
const activeAction = ref("");
const transfer = ref<{ name: string; progress: number } | null>(null);
const expandedDocId = ref<string | null>(null);
const highlightedChunkId = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let loadPromise: Promise<void> | null = null;
let mounted = false;

const statusLabel: Record<string, string> = {
  pending: "排队中",
  processing: "处理中",
  done: "已就绪",
  failed: "失败",
  canceled: "已取消",
};

const stageLabel: Record<string, string> = {
  pending: "等待调度",
  loading: "读取来源",
  parsing: "解析内容",
  chunking: "生成片段",
  embedding: "生成向量",
  staging: "写入暂存",
  activating: "切换版本",
  cleanup: "清理旧向量",
  reconciling: "一致性对账",
  done: "完成",
  failed: "失败",
  canceled: "已取消",
};

const kindLabel: Record<string, string> = {
  ingest: "入库",
  reembed: "重嵌入",
  reconcile: "一致性对账",
};

function apiError(error: unknown, fallback: string): string {
  return String(
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      fallback,
  );
}

function ensureDetail(documentId: string): DetailState {
  if (!details.value[documentId]) {
    details.value[documentId] = {
      loading: false,
      chunksLoading: false,
      error: "",
      tab: "versions",
      versions: [],
      jobs: [],
      chunks: [],
      selectedVersionId: "",
    };
  }
  return details.value[documentId];
}

function isRunning(doc: DocumentItem): boolean {
  return doc.status === "pending" || doc.status === "processing";
}

function needsPolling(): boolean {
  return (
    docs.value.some((doc) => isRunning(doc) || doc.consistency_status === "pending_cleanup") ||
    Object.values(details.value).some((state) =>
      state.jobs.some((job) => job.status === "pending" || job.status === "processing"),
    )
  );
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = undefined;
  if (needsPolling()) {
    pollTimer = setTimeout(() => void load(), 2500);
  }
}

async function loadDetails(doc: DocumentItem, keepSelection = true) {
  const state = ensureDetail(doc.id);
  state.loading = true;
  state.error = "";
  try {
    const [versions, jobs] = await Promise.all([
      docApi.versions(kbId, doc.id),
      docApi.jobs(kbId, doc.id),
    ]);
    state.versions = versions;
    state.jobs = jobs;
    const currentSelection = keepSelection
      ? versions.find((version) => version.id === state.selectedVersionId)?.id
      : "";
    state.selectedVersionId =
      currentSelection || doc.active_version_id || versions[0]?.id || "";
    await loadChunks(doc);
  } catch (error) {
    state.error = apiError(error, "文档详情加载失败");
  } finally {
    state.loading = false;
  }
}

async function loadChunks(doc: DocumentItem) {
  const state = ensureDetail(doc.id);
  state.chunksLoading = true;
  try {
    state.chunks = state.selectedVersionId
      ? await docApi.chunks(kbId, doc.id, state.selectedVersionId)
      : [];
  } catch (error) {
    state.error = apiError(error, "原文片段加载失败");
  } finally {
    state.chunksLoading = false;
  }
}

function load(initial = false): Promise<void> {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    if (initial) loading.value = true;
    loadError.value = "";
    try {
      docs.value = await docApi.list(kbId);
      if (expandedDocId.value) {
        const expanded = docs.value.find((doc) => doc.id === expandedDocId.value);
        if (expanded) await loadDetails(expanded, true);
      }
      if (!needsPolling()) await store.refresh();
    } catch (error) {
      loadError.value = apiError(error, "文档列表加载失败");
    } finally {
      loading.value = false;
    }
  })().finally(() => {
    loadPromise = null;
    schedulePoll();
  });
  return loadPromise;
}

async function reloadAfterMutation(): Promise<void> {
  if (loadPromise) await loadPromise;
  await load();
}

async function toggleDocDetail(doc: DocumentItem) {
  if (expandedDocId.value === doc.id) {
    expandedDocId.value = null;
    return;
  }
  ensureDetail(doc.id);
  expandedDocId.value = doc.id;
  await loadDetails(doc, false);
}

async function onFiles(files: FileList | File[] | null) {
  if (!files || files.length === 0) return;
  busy.value = true;
  let uploaded = 0;
  for (const file of Array.from(files)) {
    transfer.value = { name: file.name, progress: 0 };
    try {
      await docApi.upload(kbId, file, (progress) => {
        if (transfer.value?.name === file.name) transfer.value.progress = progress;
      });
      uploaded += 1;
    } catch (error) {
      show(`${file.name}：${apiError(error, "上传失败")}`);
    }
  }
  busy.value = false;
  transfer.value = null;
  await reloadAfterMutation();
  if (uploaded) show(`${uploaded} 个文件已提交入库`);
}

async function onFileInput(event: Event) {
  const input = event.target as HTMLInputElement;
  await onFiles(input.files);
  input.value = "";
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  void onFiles(event.dataTransfer?.files ?? null);
}

async function ingestUrl() {
  const targetUrl = url.value.trim();
  if (!targetUrl || busy.value) return;
  busy.value = true;
  try {
    await docApi.ingestUrl(kbId, targetUrl);
    url.value = "";
    await reloadAfterMutation();
    show("网页已提交入库");
  } catch (error) {
    show(apiError(error, "URL 提交失败"));
  } finally {
    busy.value = false;
  }
}

function openVersionFile(documentId: string) {
  document.getElementById(`version-file-${documentId}`)?.click();
}

async function uploadVersion(doc: DocumentItem, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  activeAction.value = `version:${doc.id}`;
  transfer.value = { name: file.name, progress: 0 };
  try {
    await docApi.uploadVersion(kbId, doc.id, file, (progress) => {
      if (transfer.value?.name === file.name) transfer.value.progress = progress;
    });
    ensureDetail(doc.id).tab = "versions";
    await reloadAfterMutation();
    show("新文件版本已提交");
  } catch (error) {
    show(apiError(error, "新版本上传失败"));
  } finally {
    transfer.value = null;
    activeAction.value = "";
  }
}

async function ingestUrlVersion(doc: DocumentItem) {
  const targetUrl = (versionUrls.value[doc.id] || "").trim();
  if (!targetUrl) return;
  activeAction.value = `url-version:${doc.id}`;
  try {
    await docApi.ingestUrlVersion(kbId, doc.id, targetUrl);
    versionUrls.value[doc.id] = "";
    ensureDetail(doc.id).tab = "versions";
    await reloadAfterMutation();
    show("网页新版本已提交");
  } catch (error) {
    show(apiError(error, "网页版本提交失败"));
  } finally {
    activeAction.value = "";
  }
}

async function remove(doc: DocumentItem) {
  if (!confirm(`删除文档「${doc.name}」？文档版本、向量与相关引用来源将一并删除。`)) {
    return;
  }
  activeAction.value = `delete:${doc.id}`;
  try {
    await docApi.remove(kbId, doc.id);
    if (expandedDocId.value === doc.id) expandedDocId.value = null;
    await reloadAfterMutation();
    show("文档已删除");
  } catch (error) {
    show(apiError(error, "文档删除失败"));
  } finally {
    activeAction.value = "";
  }
}

async function reembed(doc: DocumentItem) {
  activeAction.value = `reembed:${doc.id}`;
  try {
    await docApi.reembed(kbId, doc.id);
    await reloadAfterMutation();
    show("重嵌入任务已提交");
  } catch (error) {
    show(apiError(error, "重嵌入失败"));
  } finally {
    activeAction.value = "";
  }
}

async function reconcile(doc: DocumentItem) {
  activeAction.value = `reconcile:${doc.id}`;
  try {
    await docApi.reconcile(kbId, doc.id);
    ensureDetail(doc.id).tab = "jobs";
    await reloadAfterMutation();
    show("一致性对账已提交");
  } catch (error) {
    show(apiError(error, "一致性对账失败"));
  } finally {
    activeAction.value = "";
  }
}

async function cancelJob(doc: DocumentItem, job: IngestionJobItem) {
  activeAction.value = `cancel:${job.id}`;
  try {
    await docApi.cancelJob(kbId, doc.id, job.id);
    await reloadAfterMutation();
    show("已请求取消任务");
  } catch (error) {
    show(apiError(error, "取消任务失败"));
  } finally {
    activeAction.value = "";
  }
}

async function retryJob(doc: DocumentItem, job: IngestionJobItem) {
  activeAction.value = `retry:${job.id}`;
  try {
    await docApi.retryJob(kbId, doc.id, job.id);
    await reloadAfterMutation();
    show("任务已重新提交");
  } catch (error) {
    show(apiError(error, "重试任务失败"));
  } finally {
    activeAction.value = "";
  }
}

function canCancel(job: IngestionJobItem): boolean {
  return job.status === "pending" || job.status === "processing";
}

function canRetry(job: IngestionJobItem): boolean {
  return (
    (job.status === "failed" || job.status === "canceled") &&
    job.attempt < job.max_attempts
  );
}

async function showVersionSource(doc: DocumentItem, versionId: string) {
  const state = ensureDetail(doc.id);
  state.selectedVersionId = versionId;
  state.tab = "source";
  await loadChunks(doc);
}

async function openQueryTarget() {
  if (!mounted) return;
  const documentId = typeof route.query.document === "string" ? route.query.document : "";
  if (!documentId) return;
  const doc = docs.value.find((item) => item.id === documentId);
  if (!doc) return;
  ensureDetail(doc.id).tab = "source";
  expandedDocId.value = doc.id;
  await loadDetails(doc, false);
  const chunkId = typeof route.query.chunk === "string" ? route.query.chunk : "";
  highlightedChunkId.value = chunkId;
  if (chunkId) {
    await nextTick();
    document.getElementById(`chunk-${chunkId}`)?.scrollIntoView({ block: "center" });
  }
}

function fmtSize(bytes: number) {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function fmtHash(value: string) {
  return value ? value.slice(0, 12) : "-";
}

function consistencyLabel(value: string) {
  const labels: Record<string, string> = {
    consistent: "数据一致",
    pending_cleanup: "待清理",
    inconsistent: "需对账",
  };
  return labels[value] || value || "未知";
}

function boundedProgress(value: number) {
  return Math.max(0, Math.min(100, value || 0));
}

const hasDocs = computed(() => docs.value.length > 0);
const canWrite = computed(() => auth.can("doc:write"));
const canDelete = computed(() => auth.can("doc:delete"));

onMounted(async () => {
  mounted = true;
  await load(true);
  await openQueryTarget();
});

watch(
  () => [route.query.document, route.query.chunk],
  () => void openQueryTarget(),
);

onUnmounted(() => {
  mounted = false;
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div class="documents-page">
    <KbTabs :kb-id="kbId" active="documents" />

    <div v-if="loadError" class="state-banner error">
      {{ loadError }}
      <button class="btn sm" type="button" @click="load()">重试</button>
    </div>

    <section v-if="canWrite" class="ingest-panel" aria-label="文档导入">
      <button
        class="dropzone"
        :class="{ drag: dragging }"
        type="button"
        :disabled="busy"
        @click="fileInput?.click()"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <UploadCloud :size="22" aria-hidden="true" />
        <span>
          <strong>上传文件</strong>
          <small>PDF、DOCX、XLSX、Markdown、TXT、CSV、HTML</small>
        </span>
      </button>
      <input
        ref="fileInput"
        type="file"
        multiple
        hidden
        accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.csv,.htm,.html"
        @change="onFileInput"
      />

      <form class="url-ingest" @submit.prevent="ingestUrl">
        <label for="document-url"><Globe2 :size="16" aria-hidden="true" /> 网页地址</label>
        <input
          id="document-url"
          v-model="url"
          class="input"
          type="url"
          placeholder="https://example.com/policy"
          :disabled="busy"
        />
        <button class="btn primary" type="submit" :disabled="busy || !url.trim()">
          <LoaderCircle v-if="busy" class="rotating" :size="15" aria-hidden="true" />
          <Globe2 v-else :size="15" aria-hidden="true" />
          导入网页
        </button>
      </form>

      <div v-if="transfer" class="transfer-status" role="status">
        <div>
          <span>{{ transfer.name }}</span>
          <b>{{ transfer.progress }}%</b>
        </div>
        <div class="progress-track" role="progressbar" :aria-valuenow="transfer.progress">
          <span :style="{ width: `${transfer.progress}%` }"></span>
        </div>
      </div>
    </section>

    <div v-else class="state-banner permission" role="status">
      当前账号为只读权限：可以查看文档、版本、任务和原文，不能上传、更新、重嵌入或删除文档。
    </div>

    <section class="document-panel">
      <div class="panel-toolbar">
        <div>
          <h2>文档</h2>
          <span class="muted">{{ docs.length }} 个文档</span>
        </div>
        <button
          class="btn ghost sm icon-command"
          type="button"
          title="刷新文档"
          :disabled="loading"
          @click="load()"
        >
          <RefreshCw :class="{ rotating: loading }" :size="16" aria-hidden="true" />
          <span class="sr-only">刷新文档</span>
        </button>
      </div>

      <div v-if="loading" class="state-banner loading" aria-busy="true">
        <LoaderCircle class="rotating" :size="16" aria-hidden="true" />
        加载文档
      </div>
      <div v-else-if="!hasDocs" class="empty compact">
        <FileText :size="30" aria-hidden="true" />
        <p>暂无文档</p>
      </div>
      <div v-else class="table-wrap document-table-wrap">
        <table class="table document-table">
          <thead>
            <tr>
              <th>文档</th>
              <th>版本</th>
              <th>大小 / 片段</th>
              <th>状态</th>
              <th>进度</th>
              <th class="actions-cell">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="doc in docs" :key="doc.id">
              <tr class="document-row" :class="{ expanded: expandedDocId === doc.id }">
                <td data-label="文档">
                  <div class="document-identity">
                    <FileText :size="17" aria-hidden="true" />
                    <button type="button" @click="toggleDocDetail(doc)">
                      <strong>{{ doc.name }}</strong>
                      <span>{{ doc.source_type === "url" ? doc.source : doc.mime }}</span>
                    </button>
                  </div>
                </td>
                <td data-label="版本">
                  <strong>v{{ doc.version || 0 }}</strong>
                  <span v-if="doc.latest_version > doc.version" class="cell-subtext">
                    最新 v{{ doc.latest_version }}
                  </span>
                </td>
                <td data-label="大小 / 片段">
                  {{ fmtSize(doc.size_bytes) }}
                  <span class="cell-subtext">{{ doc.chunk_count }} 个片段</span>
                </td>
                <td data-label="状态">
                  <span class="status-line" :class="doc.status" :title="doc.error">
                    <LoaderCircle
                      v-if="isRunning(doc)"
                      class="rotating"
                      :size="14"
                      aria-hidden="true"
                    />
                    <CheckCircle2
                      v-else-if="doc.status === 'done'"
                      :size="14"
                      aria-hidden="true"
                    />
                    <CircleX
                      v-else-if="doc.status === 'canceled'"
                      :size="14"
                      aria-hidden="true"
                    />
                    <AlertTriangle v-else :size="14" aria-hidden="true" />
                    {{ statusLabel[doc.status] || doc.status }}
                  </span>
                  <span
                    class="consistency"
                    :class="doc.consistency_status"
                    :title="doc.cleanup_error"
                  >
                    {{ consistencyLabel(doc.consistency_status) }}
                  </span>
                </td>
                <td data-label="进度">
                  <div class="row-progress">
                    <div class="progress-track" role="progressbar" :aria-valuenow="doc.progress">
                      <span :style="{ width: `${boundedProgress(doc.progress)}%` }"></span>
                    </div>
                    <span>{{ boundedProgress(doc.progress) }}%</span>
                  </div>
                </td>
                <td class="actions-cell" data-label="操作">
                  <button
                    class="btn ghost sm icon-command"
                    type="button"
                    :title="expandedDocId === doc.id ? '收起详情' : '查看详情'"
                    @click="toggleDocDetail(doc)"
                  >
                    <ChevronUp
                      v-if="expandedDocId === doc.id"
                      :size="16"
                      aria-hidden="true"
                    />
                    <ChevronDown v-else :size="16" aria-hidden="true" />
                    <span class="sr-only">查看详情</span>
                  </button>
                  <button
                    v-if="canWrite"
                    class="btn ghost sm icon-command"
                    type="button"
                    title="重新嵌入"
                    :disabled="isRunning(doc) || !!activeAction"
                    @click="reembed(doc)"
                  >
                    <RotateCw :size="16" aria-hidden="true" />
                    <span class="sr-only">重新嵌入</span>
                  </button>
                  <button
                    v-if="canDelete"
                    class="btn ghost sm icon-command danger-text"
                    type="button"
                    title="删除文档"
                    :disabled="isRunning(doc) || !!activeAction"
                    @click="remove(doc)"
                  >
                    <Trash2 :size="16" aria-hidden="true" />
                    <span class="sr-only">删除文档</span>
                  </button>
                </td>
              </tr>

              <tr v-if="expandedDocId === doc.id" class="detail-row">
                <td colspan="6">
                  <div class="detail-shell">
                    <div class="detail-toolbar">
                      <div class="detail-tabs" role="tablist" aria-label="文档详情">
                        <button
                          type="button"
                          :class="{ active: details[doc.id].tab === 'versions' }"
                          @click="details[doc.id].tab = 'versions'"
                        >
                          <History :size="15" aria-hidden="true" /> 版本
                        </button>
                        <button
                          type="button"
                          :class="{ active: details[doc.id].tab === 'jobs' }"
                          @click="details[doc.id].tab = 'jobs'"
                        >
                          <Wrench :size="15" aria-hidden="true" /> 任务
                        </button>
                        <button
                          type="button"
                          :class="{ active: details[doc.id].tab === 'source' }"
                          @click="details[doc.id].tab = 'source'"
                        >
                          <FileText :size="15" aria-hidden="true" /> 原文
                        </button>
                      </div>

                      <div v-if="canWrite" class="version-actions">
                        <input
                          :id="`version-file-${doc.id}`"
                          class="sr-only"
                          type="file"
                          accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.csv,.htm,.html"
                          @change="uploadVersion(doc, $event)"
                        />
                        <button
                          class="btn sm"
                          type="button"
                          :disabled="isRunning(doc) || !!activeAction"
                          @click="openVersionFile(doc.id)"
                        >
                          <UploadCloud :size="15" aria-hidden="true" /> 新文件版本
                        </button>
                        <div class="url-version">
                          <input
                            v-model="versionUrls[doc.id]"
                            class="input"
                            type="url"
                            placeholder="新网页版本 URL"
                            :disabled="isRunning(doc) || !!activeAction"
                            @keyup.enter="ingestUrlVersion(doc)"
                          />
                          <button
                            class="btn sm"
                            type="button"
                            title="提交网页新版本"
                            :disabled="
                              isRunning(doc) || !!activeAction || !versionUrls[doc.id]?.trim()
                            "
                            @click="ingestUrlVersion(doc)"
                          >
                            <Globe2 :size="15" aria-hidden="true" />
                            <span class="sr-only">提交网页新版本</span>
                          </button>
                        </div>
                        <button
                          class="btn ghost sm"
                          type="button"
                          title="执行数据与向量对账"
                          :disabled="isRunning(doc) || !doc.active_version_id || !!activeAction"
                          @click="reconcile(doc)"
                        >
                          <ShieldCheck :size="16" aria-hidden="true" />
                          <span>一致性对账</span>
                        </button>
                      </div>
                    </div>

                    <div v-if="details[doc.id].error" class="state-banner error">
                      {{ details[doc.id].error }}
                      <button class="btn sm" type="button" @click="loadDetails(doc, true)">
                        重试
                      </button>
                    </div>
                    <div v-if="details[doc.id].loading" class="detail-loading" aria-busy="true">
                      <LoaderCircle class="rotating" :size="16" aria-hidden="true" />
                      加载详情
                    </div>

                    <div v-else-if="details[doc.id].tab === 'versions'" class="version-list">
                      <div
                        v-for="version in details[doc.id].versions"
                        :key="version.id"
                        class="version-row"
                      >
                        <div class="version-mark" :class="{ active: version.is_active }">
                          v{{ version.version_number }}
                        </div>
                        <div class="version-main">
                          <strong>{{ version.name }}</strong>
                          <span>
                            {{ fmtTime(version.created_at) }} · {{ version.chunk_count }} 个片段 ·
                            SHA-256 {{ fmtHash(version.content_hash) }}
                          </span>
                          <span v-if="version.error" class="inline-error">{{ version.error }}</span>
                        </div>
                        <div class="version-model mono">
                          {{ version.embedding_provider }} / {{ version.embedding_model }} /
                          {{ version.embedding_dim }}d
                        </div>
                        <span class="status-line" :class="version.status">
                          {{ statusLabel[version.status] || version.status }}
                        </span>
                        <button
                          class="btn ghost sm"
                          type="button"
                          @click="showVersionSource(doc, version.id)"
                        >
                          <Eye :size="15" aria-hidden="true" /> 查看原文
                        </button>
                      </div>
                      <div v-if="details[doc.id].versions.length === 0" class="empty compact">
                        暂无版本
                      </div>
                    </div>

                    <div v-else-if="details[doc.id].tab === 'jobs'" class="job-list">
                      <div
                        v-for="job in details[doc.id].jobs"
                        :key="job.id"
                        class="job-row"
                      >
                        <div>
                          <strong>{{ kindLabel[job.kind] || job.kind }}</strong>
                          <span>{{ fmtTime(job.created_at) }} · 第 {{ job.attempt }} 次尝试</span>
                        </div>
                        <div>
                          <span class="status-line" :class="job.status">
                            {{ statusLabel[job.status] || job.status }}
                          </span>
                          <span class="cell-subtext">{{ stageLabel[job.stage] || job.stage }}</span>
                        </div>
                        <div class="job-progress">
                          <div class="progress-track" role="progressbar" :aria-valuenow="job.progress">
                            <span :style="{ width: `${boundedProgress(job.progress)}%` }"></span>
                          </div>
                          <span>{{ boundedProgress(job.progress) }}%</span>
                        </div>
                        <div class="job-actions">
                          <button
                            v-if="canWrite && canCancel(job)"
                            class="btn sm"
                            type="button"
                            :disabled="!!activeAction"
                            @click="cancelJob(doc, job)"
                          >
                            <CircleX :size="15" aria-hidden="true" /> 取消
                          </button>
                          <button
                            v-if="canWrite && canRetry(job)"
                            class="btn sm"
                            type="button"
                            :disabled="!!activeAction"
                            @click="retryJob(doc, job)"
                          >
                            <Play :size="15" aria-hidden="true" /> 重试
                          </button>
                        </div>
                        <div v-if="job.error" class="job-error">{{ job.error }}</div>
                      </div>
                      <div v-if="details[doc.id].jobs.length === 0" class="empty compact">
                        暂无任务
                      </div>
                    </div>

                    <div v-else class="source-view">
                      <div class="source-toolbar">
                        <label :for="`source-version-${doc.id}`">版本</label>
                        <select
                          :id="`source-version-${doc.id}`"
                          v-model="details[doc.id].selectedVersionId"
                          class="input"
                          @change="loadChunks(doc)"
                        >
                          <option
                            v-for="version in details[doc.id].versions"
                            :key="version.id"
                            :value="version.id"
                          >
                            v{{ version.version_number }}{{ version.is_active ? "（当前）" : "" }}
                          </option>
                        </select>
                        <span>{{ details[doc.id].chunks.length }} 个片段</span>
                      </div>
                      <div v-if="details[doc.id].chunksLoading" class="detail-loading">
                        <LoaderCircle class="rotating" :size="16" aria-hidden="true" />
                        加载原文
                      </div>
                      <div v-else class="chunk-list">
                        <article
                          v-for="chunk in details[doc.id].chunks"
                          :id="`chunk-${chunk.id}`"
                          :key="chunk.id"
                          class="chunk-row"
                          :class="{ highlighted: highlightedChunkId === chunk.id }"
                        >
                          <div class="chunk-meta">
                            <span>#{{ chunk.chunk_index + 1 }}</span>
                            <span v-if="chunk.page">第 {{ chunk.page }} 页</span>
                            <span>{{ chunk.char_count }} 字符</span>
                            <span v-if="chunk.injection_risk" class="risk-label">
                              <AlertTriangle :size="13" aria-hidden="true" /> 内容指令风险
                            </span>
                          </div>
                          <p>{{ chunk.content }}</p>
                        </article>
                        <div v-if="details[doc.id].chunks.length === 0" class="empty compact">
                          该版本暂无原文片段
                        </div>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.documents-page {
  letter-spacing: 0;
}
.ingest-panel {
  position: relative;
  display: grid;
  grid-template-columns: minmax(260px, 0.8fr) minmax(360px, 1.2fr);
  gap: var(--space-4);
  align-items: center;
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}
.dropzone {
  min-width: 0;
  min-height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px dashed var(--border-strong);
  border-radius: 6px;
  background: var(--panel-2);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.dropzone:hover,
.dropzone:focus-visible,
.dropzone.drag {
  border-color: var(--primary);
  background: var(--primary-soft);
  outline: none;
}
.dropzone:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.dropzone strong,
.dropzone small {
  display: block;
}
.dropzone small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.url-ingest {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: center;
}
.url-ingest label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.transfer-status {
  grid-column: 1 / -1;
  padding-top: var(--space-2);
  border-top: 1px solid var(--border);
}
.transfer-status > div:first-child {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: 5px;
  color: var(--muted);
  font-size: 12px;
}
.transfer-status span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.progress-track {
  height: 5px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--border);
}
.progress-track > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--primary);
  transition: width 180ms ease;
}
.document-panel {
  min-height: 280px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}
.document-panel > .panel-toolbar {
  min-height: 58px;
}
.document-table-wrap {
  border-radius: 0;
}
.document-table {
  width: 100%;
  table-layout: fixed;
}
.document-table th:nth-child(1) {
  width: 28%;
}
.document-table th:nth-child(2) {
  width: 10%;
}
.document-table th:nth-child(3) {
  width: 13%;
}
.document-table th:nth-child(4) {
  width: 16%;
}
.document-table th:nth-child(5) {
  width: 17%;
}
.document-table th:nth-child(6) {
  width: 16%;
}
.document-row.expanded td {
  background: var(--panel-2);
}
.document-identity {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.document-identity > svg {
  flex: 0 0 auto;
  color: var(--muted);
}
.document-identity button {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.document-identity button:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 3px;
}
.document-identity strong,
.document-identity span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.document-identity span {
  max-width: 100%;
  margin-top: 2px;
  color: var(--muted);
  font-size: 11px;
}
.status-line {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.status-line.done {
  color: var(--success);
}
.status-line.failed,
.status-line.canceled {
  color: var(--danger);
}
.status-line.pending,
.status-line.processing {
  color: var(--warn);
}
.consistency {
  display: block;
  width: fit-content;
  margin-top: 4px;
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--muted);
  font-size: 10px;
}
.consistency.consistent {
  color: var(--success);
  border-color: color-mix(in srgb, var(--success) 32%, var(--border));
}
.consistency.pending_cleanup,
.consistency.inconsistent {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 32%, var(--border));
}
.row-progress {
  display: grid;
  grid-template-columns: minmax(56px, 1fr) 34px;
  align-items: center;
  gap: 7px;
  color: var(--muted);
  font-size: 11px;
}
.document-row .actions-cell {
  white-space: nowrap;
}
.document-row .actions-cell .btn {
  margin-left: 2px;
}
.detail-row > td {
  padding: 0 !important;
  border-bottom: 1px solid var(--border-strong) !important;
}
.detail-shell {
  min-width: 0;
  background: var(--bg);
}
.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  min-height: 58px;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--border);
}
.detail-tabs {
  flex: 0 0 auto;
  display: inline-grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--panel-2);
}
.detail-tabs button {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.detail-tabs button.active {
  background: var(--panel);
  color: var(--text);
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}
.version-actions {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.url-version {
  min-width: 180px;
  max-width: 300px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px;
}
.url-version .input {
  min-height: 32px;
  height: 32px;
}
.detail-loading {
  min-height: 110px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  color: var(--muted);
  font-size: 12px;
}
.version-list,
.job-list,
.chunk-list {
  display: flex;
  flex-direction: column;
}
.version-row {
  display: grid;
  grid-template-columns: 48px minmax(180px, 1.5fr) minmax(160px, 0.8fr) 82px auto;
  align-items: center;
  gap: var(--space-3);
  min-height: 66px;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--border);
}
.version-mark {
  width: 38px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.version-mark.active {
  color: var(--success);
  border-color: color-mix(in srgb, var(--success) 40%, var(--border));
  background: color-mix(in srgb, var(--success) 8%, var(--panel));
}
.version-main,
.version-main strong,
.version-main span {
  min-width: 0;
}
.version-main strong,
.version-main span {
  display: block;
}
.version-main span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.version-main .inline-error,
.inline-error {
  color: var(--danger);
}
.version-model {
  min-width: 0;
  color: var(--muted);
  font-size: 10px;
  overflow-wrap: anywhere;
}
.job-row {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) 120px minmax(150px, 0.8fr) auto;
  align-items: center;
  gap: var(--space-3);
  min-height: 62px;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--border);
}
.job-row > div:first-child strong,
.job-row > div:first-child span {
  display: block;
}
.job-row > div:first-child span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}
.job-progress {
  display: grid;
  grid-template-columns: minmax(60px, 1fr) 34px;
  align-items: center;
  gap: 7px;
  color: var(--muted);
  font-size: 11px;
}
.job-actions {
  display: flex;
  justify-content: flex-end;
  gap: 5px;
}
.job-error {
  grid-column: 1 / -1;
  padding: 6px 8px;
  border-left: 2px solid var(--danger);
  background: color-mix(in srgb, var(--danger) 6%, transparent);
  color: var(--danger);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.source-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 50px;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
}
.source-toolbar .input {
  width: min(220px, 100%);
  min-height: 32px;
  height: 32px;
}
.chunk-row {
  min-width: 0;
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
  scroll-margin: 120px;
}
.chunk-row.highlighted {
  background: color-mix(in srgb, var(--primary) 10%, var(--panel));
  box-shadow: inset 3px 0 var(--primary);
}
.chunk-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  color: var(--muted);
  font-size: 10px;
}
.chunk-row p {
  margin: 8px 0 0;
  color: var(--text);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.risk-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--warn);
}

@media (max-width: 980px) {
  .detail-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .version-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .url-version {
    flex: 1 1 220px;
    max-width: none;
  }
  .version-row {
    grid-template-columns: 48px minmax(180px, 1fr) 90px auto;
  }
  .version-model {
    grid-column: 2 / -1;
  }
}

@media (max-width: 820px) {
  .ingest-panel {
    grid-template-columns: 1fr;
  }
  .transfer-status {
    grid-column: 1;
  }
  .document-table-wrap {
    overflow: visible;
  }
  .document-table,
  .document-table tbody {
    display: block;
    width: 100%;
    min-width: 0;
  }
  .document-table thead {
    display: none;
  }
  .document-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 5px var(--space-2);
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  .document-row td {
    display: grid;
    grid-template-columns: 92px minmax(0, 1fr);
    align-items: start;
    min-height: 28px;
    height: auto;
    padding: 3px 0;
    border: 0;
    text-align: left !important;
    white-space: normal;
  }
  .document-row td::before {
    content: attr(data-label);
    color: var(--muted);
    font-size: 11px;
  }
  .document-row td:first-child {
    grid-column: 1;
    display: block;
  }
  .document-row td:first-child::before,
  .document-row .actions-cell::before {
    display: none;
  }
  .document-row .actions-cell {
    grid-column: 2;
    grid-row: 1;
    display: flex;
    justify-content: flex-end;
  }
  .document-row td:nth-child(2),
  .document-row td:nth-child(3),
  .document-row td:nth-child(4),
  .document-row td:nth-child(5) {
    grid-column: 1 / -1;
  }
  .detail-row,
  .detail-row > td {
    display: block;
    width: 100%;
  }
  .version-row,
  .job-row {
    grid-template-columns: 48px minmax(0, 1fr) auto;
  }
  .version-model,
  .version-row > .status-line,
  .job-row > div:nth-child(2),
  .job-progress,
  .job-error {
    grid-column: 2 / -1;
  }
  .version-row > .btn,
  .job-actions {
    grid-column: 3;
    grid-row: 1;
  }
}

@media (max-width: 560px) {
  .ingest-panel {
    padding: var(--space-3);
  }
  .url-ingest {
    grid-template-columns: 1fr auto;
  }
  .url-ingest label {
    grid-column: 1 / -1;
  }
  .detail-tabs {
    width: 100%;
  }
  .version-actions > .btn:first-of-type,
  .url-version {
    width: 100%;
    flex-basis: 100%;
  }
  .version-row,
  .job-row {
    grid-template-columns: 42px minmax(0, 1fr);
    padding: var(--space-3);
  }
  .version-row > .btn,
  .job-actions {
    grid-column: 2;
    grid-row: auto;
    justify-content: flex-start;
  }
  .source-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .source-toolbar .input {
    width: 100%;
  }
}
</style>
