<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterView, useRouter } from "vue-router";
import { authApi, clearAuthToken, getAuthToken, http, setAuthToken } from "@/api/client";
import type { AuthUser, TenantInfo } from "@/api/types";
import { useToast } from "@/composables/toast";
import { applyTheme, resolveInitialTheme, toggleTheme, type ThemeMode } from "@/composables/theme";

const router = useRouter();
const { message, show } = useToast();
const info = ref<string>("");
const user = ref<AuthUser | null>(null);
const tenant = ref<TenantInfo | null>(null);
const email = ref("admin@example.com");
const password = ref("ChangeMe123!");
const loggingIn = ref(false);
const loginError = ref("");
const healthLoading = ref(true);
const theme = ref<ThemeMode>(resolveInitialTheme());

function onToggleTheme() {
  theme.value = toggleTheme(theme.value);
  applyTheme(theme.value);
}

async function loadHealth() {
  healthLoading.value = true;
  try {
    const { data } = await http.get("/api/health");
    info.value = `${data.llm_provider}:${data.llm_model} · 向量:${data.vector_backend}`;
  } catch {
    info.value = "后端未连接";
  } finally {
    healthLoading.value = false;
  }
}

async function loadMe() {
  if (!getAuthToken()) return;
  try {
    const me = await authApi.me();
    user.value = me.user;
    tenant.value = me.tenant;
  } catch {
    clearAuthToken();
    user.value = null;
    tenant.value = null;
  }
}

async function login() {
  if (!email.value.trim() || !password.value) return;
  loggingIn.value = true;
  loginError.value = "";
  try {
    const payload = await authApi.login(email.value.trim(), password.value);
    setAuthToken(payload.access_token);
    user.value = payload.user;
    tenant.value = payload.tenant;
    show("登录成功");
    await router.push("/");
  } catch (e: any) {
    loginError.value = e?.response?.data?.detail || "登录失败，请检查邮箱与密码";
    show(loginError.value);
  } finally {
    loggingIn.value = false;
  }
}

function logout() {
  authApi.logout();
  user.value = null;
  tenant.value = null;
  router.push("/");
}

onMounted(async () => {
  applyTheme(theme.value);
  await loadHealth();
  await loadMe();
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
      <span v-if="tenant" class="pill">租户：{{ tenant.name }}</span>
      <span v-if="user" class="pill">用户：{{ user.display_name || user.email }}</span>
      <span class="pill" :class="{ muted: healthLoading }">{{ healthLoading ? "连接中…" : info }}</span>
      <button
        class="btn ghost sm theme-toggle"
        type="button"
        :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'"
        :title="theme === 'dark' ? '切换浅色' : '切换深色'"
        @click="onToggleTheme"
      >
        {{ theme === "dark" ? "☀️" : "🌙" }}
      </button>
      <button v-if="user" class="btn ghost sm" @click="logout">退出</button>
    </header>
    <main v-if="user" class="content">
      <div class="container">
        <RouterView />
      </div>
    </main>
    <main v-else class="content auth-page">
      <div class="card auth-card">
        <div class="auth-logo">📚</div>
        <h1>登录 Enterprise RAG</h1>
        <p class="muted">使用租户账号进入企业知识库。所有知识库、文档、会话都会按租户隔离。</p>
        <div v-if="loginError" class="state-banner error">{{ loginError }}</div>
        <div class="field">
          <label>邮箱</label>
          <input v-model="email" class="input" autocomplete="username" @keyup.enter="login" />
        </div>
        <div class="field">
          <label>密码</label>
          <input
            v-model="password"
            class="input"
            type="password"
            autocomplete="current-password"
            @keyup.enter="login"
          />
        </div>
        <button class="btn primary auth-submit" :disabled="loggingIn" @click="login">
          <span v-if="loggingIn" class="spin"></span> 登录
        </button>
        <p class="muted mono">
          本地默认演示账号来自环境变量 BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD。
        </p>
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
.auth-page {
  display: grid;
  place-items: center;
}
.auth-card {
  width: 420px;
  max-width: 92vw;
  padding: var(--space-8);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.auth-logo {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--primary), var(--primary-gradient-end));
  color: var(--text-on-primary);
  font-size: 22px;
}
.auth-card h1 {
  margin: 16px 0 4px;
  font-size: 22px;
}
.auth-submit {
  width: 100%;
  justify-content: center;
  margin: 4px 0 14px;
}
</style>
