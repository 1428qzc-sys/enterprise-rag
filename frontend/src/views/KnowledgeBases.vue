<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { kbApi } from "@/api/client";
import { useKbStore } from "@/stores/kb";
import { useToast } from "@/composables/toast";

const store = useKbStore();
const router = useRouter();
const { show } = useToast();

const showModal = ref(false);
const name = ref("");
const description = ref("");
const creating = ref(false);

onMounted(() => store.refresh());

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
  } catch (e: any) {
    show(e?.response?.data?.detail || "创建失败");
  } finally {
    creating.value = false;
  }
}

async function remove(id: string, kbName: string, ev: Event) {
  ev.stopPropagation();
  if (!confirm(`确认删除知识库「${kbName}」？其文档、向量与对话都会被清除。`)) return;
  await kbApi.remove(id);
  await store.refresh();
  show("已删除");
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
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>工作台</h1>
        <p>创建并管理相互隔离的知识库，导入文档后即可进行检索增强问答。</p>
      </div>
      <button class="btn primary" :disabled="store.loading" @click="showModal = true">
        + 新建知识库
      </button>
    </div>

    <div v-if="store.error" class="state-banner error">
      {{ store.error }}
      <button class="btn sm" style="margin-left: auto" @click="store.refresh()">重试</button>
    </div>

    <div v-if="!store.loading && store.kbs.length" class="dashboard-stats">
      <div class="card stat-card">
        <span class="label">知识库</span>
        <span class="value">{{ stats.kbCount }}</span>
      </div>
      <div class="card stat-card">
        <span class="label">文档总数</span>
        <span class="value">{{ stats.docCount }}</span>
      </div>
      <div class="card stat-card">
        <span class="label">向量片段</span>
        <span class="value">{{ stats.chunkCount }}</span>
      </div>
    </div>

    <div v-if="store.loading" class="skeleton-grid" aria-busy="true">
      <div v-for="n in 3" :key="n" class="skeleton-card"></div>
    </div>

    <div v-else-if="store.kbs.length === 0 && !store.error" class="empty card" style="padding: var(--space-8)">
      <div class="big">📚</div>
      <p>还没有知识库，点击右上角「新建知识库」开始吧。</p>
    </div>

    <div v-else class="kb-grid">
      <div
        v-for="kb in store.kbs"
        :key="kb.id"
        class="card kb-card"
        @click="router.push(`/kb/${kb.id}/chat`)"
      >
        <div class="row" style="justify-content: space-between">
          <h3>{{ kb.name }}</h3>
          <button class="btn ghost sm" title="删除" @click="remove(kb.id, kb.name, $event)">🗑</button>
        </div>
        <div class="desc">{{ kb.description || "暂无描述" }}</div>
        <div class="stats">
          <span><b>{{ kb.document_count }}</b> 文档</span>
          <span><b>{{ kb.chunk_count }}</b> 片段</span>
          <span class="spacer" style="flex: 1"></span>
          <span>{{ fmt(kb.created_at) }}</span>
        </div>
      </div>
    </div>

    <div v-if="showModal" class="modal-mask" @click.self="showModal = false">
      <div class="card modal">
        <h3>新建知识库</h3>
        <div class="field">
          <label>名称</label>
          <input v-model="name" class="input" placeholder="例如：员工手册知识库" @keyup.enter="create" />
        </div>
        <div class="field">
          <label>描述（可选）</label>
          <textarea v-model="description" class="textarea" rows="3" placeholder="用途说明"></textarea>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="showModal = false">取消</button>
          <button class="btn primary" :disabled="creating || !name.trim()" @click="create">
            <span v-if="creating" class="spin"></span> 创建
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
