<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { useKbStore } from "@/stores/kb";

const props = defineProps<{ kbId: string; active: "documents" | "chat" }>();
const store = useKbStore();
const kb = computed(() => store.find(props.kbId));

onMounted(() => store.ensureLoaded());
</script>

<template>
  <div class="kb-subnav">
    <RouterLink to="/" class="btn ghost sm">← 知识库</RouterLink>
    <span class="title">{{ kb?.name || "知识库" }}</span>
    <span v-if="kb" class="pill mono">{{ kb.embedding_model }} · {{ kb.embedding_dim }}d</span>
    <div class="spacer" style="flex: 1"></div>
    <div class="tabs">
      <RouterLink :to="`/kb/${kbId}/documents`" :class="{ active: active === 'documents' }">
        文档管理
      </RouterLink>
      <RouterLink :to="`/kb/${kbId}/chat`" :class="{ active: active === 'chat' }">
        智能问答
      </RouterLink>
    </div>
  </div>
</template>
