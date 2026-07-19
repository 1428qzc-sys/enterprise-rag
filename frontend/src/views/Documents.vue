<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";
import { docApi } from "@/api/client";
import type { DocumentItem } from "@/api/types";
import KbTabs from "@/components/KbTabs.vue";
import { useToast } from "@/composables/toast";
import { useKbStore } from "@/stores/kb";

const route = useRoute();
const kbId = route.params.id as string;
const { show } = useToast();
const store = useKbStore();

const docs = ref<DocumentItem[]>([]);
const dragging = ref(false);
const url = ref("");
const busy = ref(false);
const loading = ref(true);
const loadError = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
let timer: ReturnType<typeof setInterval> | undefined;

const statusLabel: Record<string, string> = {
  pending: "排队中",
  processing: "解析中",
  done: "已就绪",
  failed: "失败",
};

async function load() {
  loadError.value = "";
  try {
    docs.value = await docApi.list(kbId);
  } catch (e: unknown) {
    const detail =
      (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      "文档列表加载失败";
    loadError.value = String(detail);
    return;
  } finally {
    loading.value = false;
  }
  const processing = docs.value.some((d) => d.status === "pending" || d.status === "processing");
  if (processing && !timer) {
    timer = setInterval(load, 2500);
  } else if (!processing && timer) {
    clearInterval(timer);
    timer = undefined;
    store.refresh();
  }
}

async function onFiles(files: FileList | null) {
  if (!files || files.length === 0) return;
  busy.value = true;
  try {
    for (const file of Array.from(files)) {
      try {
        await docApi.upload(kbId, file);
      } catch (e: any) {
        show(`${file.name}：${e?.response?.data?.detail || "上传失败"}`);
      }
    }
    await load();
    show("已上传，正在后台解析入库");
  } finally {
    busy.value = false;
  }
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  onFiles(e.dataTransfer?.files ?? null);
}

async function ingestUrl() {
  const u = url.value.trim();
  if (!u) return;
  busy.value = true;
  try {
    await docApi.ingestUrl(kbId, u);
    url.value = "";
    await load();
    show("已提交网页抓取任务");
  } catch (e: any) {
    show(e?.response?.data?.detail || "URL 提交失败");
  } finally {
    busy.value = false;
  }
}

async function remove(doc: DocumentItem) {
  if (!confirm(`删除文档「${doc.name}」？`)) return;
  await docApi.remove(kbId, doc.id);
  await load();
  show("已删除");
}

async function reembed(doc: DocumentItem) {
  await docApi.reembed(kbId, doc.id);
  await load();
  show("已触发重嵌入");
}

function fmtSize(n: number) {
  if (!n) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const hasDocs = computed(() => docs.value.length > 0);
const expandedDocId = ref<string | null>(null);

function toggleDocDetail(docId: string) {
  expandedDocId.value = expandedDocId.value === docId ? null : docId;
}

function fmtTime(d: string) {
  return new Date(d).toLocaleString("zh-CN", { hour12: false });
}

onMounted(load);
onUnmounted(() => timer && clearInterval(timer));
</script>

<template>
  <div>
    <KbTabs :kb-id="kbId" active="documents" />

    <div v-if="loadError" class="state-banner error">
      {{ loadError }}
      <button class="btn sm" style="margin-left: auto" @click="load()">重试</button>
    </div>

    <div class="row" style="gap: var(--space-4); align-items: stretch; margin-bottom: var(--space-5)">
      <div
        class="dropzone"
        :class="{ drag: dragging }"
        style="flex: 2"
        @click="fileInput?.click()"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input
          ref="fileInput"
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.xlsx,.xls,.md,.markdown,.txt,.csv,.htm,.html"
          @change="onFiles(($event.target as HTMLInputElement).files)"
        />
        <div style="font-size: 26px">⬆️</div>
        <div>点击或拖拽文件到此上传</div>
        <div class="mono" style="margin-top: 6px">支持 PDF / Word / Excel / Markdown / TXT / HTML</div>
      </div>

      <div class="card" style="flex: 1; padding: 16px; display: flex; flex-direction: column; gap: 10px">
        <label class="muted" style="font-size: 12px">从网页 URL 导入</label>
        <input v-model="url" class="input" placeholder="https://..." @keyup.enter="ingestUrl" />
        <button class="btn primary" :disabled="busy || !url.trim()" @click="ingestUrl">
          <span v-if="busy" class="spin"></span> 抓取并入库
        </button>
      </div>
    </div>

    <div class="card" style="position: relative">
      <div v-if="loading" class="state-banner loading"><span class="spin"></span> 加载文档…</div>
      <div v-else-if="!hasDocs" class="empty">
        <div class="big">📄</div>
        <p>还没有文档，上传或抓取后会自动解析、分块并向量化。</p>
      </div>
      <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>文档</th>
            <th>类型</th>
            <th>大小</th>
            <th>片段</th>
            <th>状态</th>
            <th style="text-align: right">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="doc in docs" :key="doc.id">
            <tr
              class="doc-row-expandable"
              :class="{ 'is-expanded': expandedDocId === doc.id }"
              @click="toggleDocDetail(doc.id)"
            >
              <td>
                <div style="font-weight: 500; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ doc.name }}
                </div>
              </td>
              <td class="muted">{{ doc.source_type === "url" ? "网页" : "文件" }}</td>
              <td class="muted">{{ fmtSize(doc.size_bytes) }}</td>
              <td>{{ doc.chunk_count }}</td>
              <td>
                <span class="badge" :class="doc.status" :title="doc.error">
                  {{ statusLabel[doc.status] }}
                </span>
              </td>
              <td style="text-align: right; white-space: nowrap" @click.stop>
                <button class="btn sm ghost" title="重新嵌入" @click="reembed(doc)">↻</button>
                <button class="btn sm ghost" title="删除" @click="remove(doc)">🗑</button>
              </td>
            </tr>
            <tr v-if="expandedDocId === doc.id">
              <td colspan="6" class="doc-detail-panel">
                <dl class="doc-detail-grid">
                  <div>
                    <dt>来源</dt>
                    <dd>{{ doc.source || doc.name }}</dd>
                  </div>
                  <div>
                    <dt>MIME</dt>
                    <dd>{{ doc.mime || "—" }}</dd>
                  </div>
                  <div>
                    <dt>入库时间</dt>
                    <dd>{{ fmtTime(doc.created_at) }}</dd>
                  </div>
                  <div>
                    <dt>更新时间</dt>
                    <dd>{{ fmtTime(doc.updated_at) }}</dd>
                  </div>
                  <div v-if="doc.error">
                    <dt>错误信息</dt>
                    <dd style="color: var(--danger)">{{ doc.error }}</dd>
                  </div>
                </dl>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      </div>
    </div>
  </div>
</template>
