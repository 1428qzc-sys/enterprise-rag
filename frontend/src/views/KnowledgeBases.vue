<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight, Blocks, FileText, Library, Plus, Trash2, X } from "@lucide/vue";
import { apiErrorMessage, kbApi } from "@/api/client";
import { useKbStore } from "@/stores/kb";
import { useAuthStore } from "@/stores/auth";
import { useToast } from "@/composables/toast";

const store = useKbStore();
const auth = useAuthStore();
const router = useRouter();
const { show } = useToast();

const showModal = ref(false);
const name = ref("");
const description = ref("");
const creating = ref(false);
const deletingId = ref("");
const nameInput = ref<HTMLInputElement | null>(null);

onMounted(() => store.refresh());

async function openCreateDialog() {
  showModal.value = true;
  await nextTick();
  nameInput.value?.focus();
}

function closeCreateDialog() {
  if (!creating.value) showModal.value = false;
}

async function create() {
  if (!name.value.trim()) return;
  creating.value = true;
  try {
    const kb = await kbApi.create(name.value.trim(), description.value.trim());
    showModal.value = false;
    name.value = "";
    description.value = "";
    await store.refresh();
    show("知识库已创建");
    router.push(`/kb/${kb.id}/documents`);
  } catch (error: unknown) {
    show(apiErrorMessage(error, "创建失败"));
  } finally {
    creating.value = false;
  }
}

async function remove(id: string, kbName: string, ev: Event) {
  ev.stopPropagation();
  if (!confirm(`确认删除知识库「${kbName}」？其文档、向量与对话都会被清除。`)) return;
  deletingId.value = id;
  try {
    await kbApi.remove(id);
    await store.refresh();
    show("知识库已删除");
  } catch (error: unknown) {
    show(apiErrorMessage(error, "知识库删除失败"));
  } finally {
    deletingId.value = "";
  }
}

function fmt(d: string) {
  return new Date(d).toLocaleString("zh-CN", { hour12: false });
}

const stats = computed(() => {
  const list = store.kbs;
  return {
    kbCount: list.length,
    docCount: list.reduce((n, k) => n + (k.document_count || 0), 0),
    chunkCount: list.reduce((n, k) => n + (k.chunk_count || 0), 0),
  };
});
const canWrite = computed(() => auth.can("kb:write"));
const canDelete = computed(() => auth.can("kb:delete"));
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>工作台</h1>
        <p>创建并管理相互隔离的知识库，导入文档后即可进行检索增强问答。</p>
      </div>
      <button v-if="canWrite" class="btn primary" type="button" :disabled="store.loading" @click="openCreateDialog">
        <Plus :size="16" aria-hidden="true" /> 新建知识库
      </button>
    </div>

    <div v-if="!canWrite" class="state-banner permission" role="status">
      当前账号为只读权限：可以查看知识库、文档并进行问答，不能创建或修改知识库。
    </div>

    <div v-if="store.error" class="state-banner error">
      {{ store.error }}
      <button class="btn sm" style="margin-left: auto" @click="store.refresh()">重试</button>
    </div>

    <div v-if="!store.loading && store.kbs.length" class="dashboard-stats">
      <div class="card stat-card">
        <div class="stat-icon"><Library :size="17" aria-hidden="true" /></div>
        <div><span class="label">知识库</span><span class="value">{{ stats.kbCount }}</span></div>
      </div>
      <div class="card stat-card">
        <div class="stat-icon"><FileText :size="17" aria-hidden="true" /></div>
        <div><span class="label">文档总数</span><span class="value">{{ stats.docCount }}</span></div>
      </div>
      <div class="card stat-card">
        <div class="stat-icon"><Blocks :size="17" aria-hidden="true" /></div>
        <div><span class="label">可检索片段</span><span class="value">{{ stats.chunkCount }}</span></div>
      </div>
    </div>

    <div v-if="store.loading" class="skeleton-grid" aria-busy="true">
      <div v-for="n in 3" :key="n" class="skeleton-card"></div>
    </div>

    <div v-else-if="store.kbs.length === 0 && !store.error" class="empty card" style="padding: var(--space-8)">
      <Library class="empty-icon" :size="34" aria-hidden="true" />
      <h2>创建第一个知识库</h2>
      <p>导入企业文档后，即可检索并进行带引用的问答。</p>
      <button v-if="canWrite" class="btn primary" type="button" @click="openCreateDialog">
        <Plus :size="16" aria-hidden="true" /> 新建知识库
      </button>
    </div>

    <div v-else class="kb-grid">
      <article
        v-for="kb in store.kbs"
        :key="kb.id"
        class="card kb-card"
      >
        <div class="kb-card-head">
          <div class="kb-symbol"><Library :size="18" aria-hidden="true" /></div>
          <div class="kb-title">
            <h3>{{ kb.name }}</h3>
            <span>{{ kb.embedding_model }} · {{ kb.embedding_dim }}d</span>
          </div>
          <button
            v-if="canDelete"
            class="btn ghost sm icon-command danger-text"
            type="button"
            title="删除知识库"
            :disabled="deletingId === kb.id"
            @click="remove(kb.id, kb.name, $event)"
          >
            <span v-if="deletingId === kb.id" class="spin"></span>
            <Trash2 v-else :size="15" aria-hidden="true" />
            <span class="sr-only">删除 {{ kb.name }}</span>
          </button>
        </div>
        <div class="desc">{{ kb.description || "暂无描述" }}</div>
        <div class="stats">
          <span><b>{{ kb.document_count }}</b> 文档</span>
          <span><b>{{ kb.chunk_count }}</b> 片段</span>
          <span class="spacer" style="flex: 1"></span>
          <span>{{ fmt(kb.created_at) }}</span>
        </div>
        <button class="kb-enter" type="button" @click="router.push(`/kb/${kb.id}/chat`)">
          进入知识库 <ArrowRight :size="15" aria-hidden="true" />
        </button>
      </article>
    </div>

    <div v-if="showModal" class="modal-mask" @click.self="closeCreateDialog" @keydown.esc="closeCreateDialog">
      <form class="card modal" role="dialog" aria-modal="true" aria-labelledby="create-kb-title" @submit.prevent="create">
        <div class="modal-title-row">
          <h3 id="create-kb-title">新建知识库</h3>
          <button class="btn ghost sm icon-command" type="button" title="关闭" :disabled="creating" @click="closeCreateDialog">
            <X :size="16" aria-hidden="true" /><span class="sr-only">关闭</span>
          </button>
        </div>
        <div class="field">
          <label for="kb-name">名称</label>
          <input id="kb-name" ref="nameInput" v-model="name" class="input" maxlength="128" required placeholder="例如：员工手册知识库" />
        </div>
        <div class="field">
          <label for="kb-description">描述（可选）</label>
          <textarea id="kb-description" v-model="description" class="textarea" rows="3" placeholder="说明该知识库收录的资料与适用范围"></textarea>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" :disabled="creating" @click="closeCreateDialog">取消</button>
          <button class="btn primary" type="submit" :disabled="creating || !name.trim()">
            <span v-if="creating" class="spin"></span> 创建
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.dashboard-stats .stat-card {
  display: grid;
  grid-template-columns: 38px 1fr;
  align-items: center;
}
.stat-icon,
.kb-symbol {
  display: grid;
  place-items: center;
  color: var(--primary);
  background: var(--primary-soft);
}
.stat-icon {
  width: 34px;
  height: 34px;
  border-radius: 9px;
}
.stat-card .label,
.stat-card .value {
  display: block;
}
.kb-card {
  cursor: default;
}
.kb-card-head {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
}
.kb-symbol {
  width: 38px;
  height: 38px;
  border-radius: 10px;
}
.kb-title {
  min-width: 0;
}
.kb-title h3,
.kb-title span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-title span {
  margin-top: 2px;
  color: var(--muted);
  font-size: 10px;
}
.kb-enter {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 0 0;
  border: 0;
  border-top: 1px solid var(--border);
  background: transparent;
  color: var(--primary-600);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.kb-enter:hover {
  color: var(--primary);
}
.empty h2 {
  margin: 8px 0 0;
  color: var(--text);
  font-size: 16px;
}
.empty p {
  margin: 4px 0 16px;
}
.empty-icon {
  color: var(--primary);
}
</style>
