import { defineStore } from "pinia";
import { ref } from "vue";
import { kbApi } from "@/api/client";
import type { KnowledgeBase } from "@/api/types";

export const useKbStore = defineStore("kb", () => {
  const kbs = ref<KnowledgeBase[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function refresh() {
    loading.value = true;
    error.value = null;
    try {
      kbs.value = await kbApi.list();
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "无法加载知识库列表，请检查网络或稍后重试";
      error.value = String(detail);
      kbs.value = [];
    } finally {
      loading.value = false;
    }
  }

  function find(id: string): KnowledgeBase | undefined {
    return kbs.value.find((k) => k.id === id);
  }

  async function ensureLoaded() {
    if (kbs.value.length === 0) await refresh();
  }

  return { kbs, loading, error, refresh, find, ensureLoaded };
});
