<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterView, useRouter } from "vue-router";
import { http } from "@/api/client";
import { useToast } from "@/composables/toast";

const router = useRouter();
const { message } = useToast();
const info = ref<string>("");

onMounted(async () => {
  try {
    const { data } = await http.get("/api/health");
    info.value = `${data.llm_provider}:${data.llm_model} · 向量:${data.vector_backend}`;
  } catch {
    info.value = "后端未连接";
  }
});
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand" style="cursor: pointer" @click="router.push('/')">
        <span class="logo">📚</span>
        <span>Enterprise RAG</span>
      </div>
      <div class="spacer"></div>
      <span class="pill">{{ info }}</span>
    </header>
    <main class="content">
      <div class="container">
        <RouterView />
      </div>
    </main>
    <Transition name="fade">
      <div v-if="message" class="toast">{{ message }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
