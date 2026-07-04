import { defineStore } from "pinia";
import { ref } from "vue";
import { kbApi } from "@/api/client";
import type { KnowledgeBase } from "@/api/types";

export const useKbStore = defineStore("kb", () => {
  const kbs = ref<KnowledgeBase[]>([]);
  const loading = ref(false);

  async function refresh() {
    loading.value = true;
    try {
      kbs.value = await kbApi.list();
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

  return { kbs, loading, refresh, find, ensureLoaded };
});
