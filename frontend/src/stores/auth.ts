import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { AuthUser, TenantInfo } from "@/api/types";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AuthUser | null>(null);
  const tenant = ref<TenantInfo | null>(null);
  const permissions = computed(() => new Set(user.value?.permissions ?? []));

  function setSession(nextUser: AuthUser, nextTenant: TenantInfo) {
    user.value = nextUser;
    tenant.value = nextTenant;
  }

  function clearSession() {
    user.value = null;
    tenant.value = null;
  }

  function can(permission: string): boolean {
    return permissions.value.has(permission);
  }

  return { user, tenant, permissions, setSession, clearSession, can };
});
