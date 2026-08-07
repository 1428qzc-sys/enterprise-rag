<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { storeToRefs } from "pinia";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { BookOpen, LogOut, Moon, Settings, Sun } from "@lucide/vue";
import {
  AUTH_EXPIRED_EVENT,
  apiErrorMessage,
  authApi,
  clearAuthToken,
  getAuthToken,
  http,
  setAuthToken,
} from "@/api/client";
import { useToast } from "@/composables/toast";
import { applyTheme, resolveInitialTheme, toggleTheme, type ThemeMode } from "@/composables/theme";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const { message, show } = useToast();
const auth = useAuthStore();
const { user, tenant } = storeToRefs(auth);
const info = ref<string>("");
const tenantSlug = ref("demo");
const email = ref("");
const password = ref("");
const loggingIn = ref(false);
const loginError = ref("");
const healthLoading = ref(true);
const theme = ref<ThemeMode>(resolveInitialTheme());
const isMockMode = ref(false);

function onToggleTheme() {
  theme.value = toggleTheme(theme.value);
  applyTheme(theme.value);
}

async function loadHealth() {
  healthLoading.value = true;
  try {
    const { data } = await http.get("/api/health");
    info.value = `${data.llm_provider}:${data.llm_model} · 向量:${data.vector_backend}`;
    isMockMode.value = data.embedding_provider === "fake" || data.llm_provider === "echo";
  } catch {
    info.value = "后端未连接";
    isMockMode.value = false;
  } finally {
    healthLoading.value = false;
  }
}

function fillDemoAccount() {
  tenantSlug.value = "demo";
  email.value = "admin@example.com";
  password.value = "ChangeMe123!";
}

async function loadMe() {
  if (!getAuthToken()) return;
  try {
    const me = await authApi.me();
    auth.setSession(me.user, me.tenant);
  } catch {
    clearAuthToken();
    auth.clearSession();
  }
}

async function login() {
  if (!tenantSlug.value.trim() || !email.value.trim() || !password.value) return;
  loggingIn.value = true;
  loginError.value = "";
  try {
    const payload = await authApi.login(
      tenantSlug.value.trim(),
      email.value.trim(),
      password.value,
    );
    setAuthToken(payload.access_token);
    auth.setSession(payload.user, payload.tenant);
    show("登录成功");
    await router.push("/");
  } catch (error: unknown) {
    loginError.value = apiErrorMessage(error, "登录失败，请检查租户、邮箱与密码");
    show(loginError.value);
  } finally {
    loggingIn.value = false;
  }
}

function logout() {
  authApi.logout();
  auth.clearSession();
  router.push("/");
}

function handleAuthExpired() {
  const wasLoggedIn = Boolean(user.value);
  auth.clearSession();
  if (wasLoggedIn) show("登录状态已失效，请重新登录");
  void router.push("/");
}

onMounted(async () => {
  window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  applyTheme(theme.value);
  await loadHealth();
  await loadMe();
});

onBeforeUnmount(() => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired));
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/" aria-label="返回知识库工作台">
        <span class="logo"><BookOpen :size="16" aria-hidden="true" /></span>
        <span>Enterprise RAG</span>
      </RouterLink>
      <div class="spacer"></div>
      <RouterLink
        v-if="user?.permissions.includes('admin:manage')"
        class="btn ghost sm topbar-action"
        to="/admin"
        aria-label="租户管理"
        title="租户管理"
      >
        <Settings :size="15" aria-hidden="true" />
        <span>租户管理</span>
      </RouterLink>
      <span v-if="tenant" class="pill tenant-pill">租户：{{ tenant.name }}</span>
      <span v-if="user" class="pill user-pill">用户：{{ user.display_name || user.email }}</span>
      <span
        class="pill health-pill"
        :class="{ muted: healthLoading, warning: isMockMode }"
        aria-live="polite"
        :title="healthLoading ? '正在连接后端' : info"
      >
        <span class="health-full">
          {{ healthLoading ? "连接中…" : isMockMode ? `Mock · ${info}` : info }}
        </span>
        <span class="health-short">
          {{ healthLoading ? "连接中" : isMockMode ? "Mock 模式" : "服务在线" }}
        </span>
      </span>
      <button
        class="btn ghost sm theme-toggle"
        type="button"
        :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'"
        :title="theme === 'dark' ? '切换浅色' : '切换深色'"
        @click="onToggleTheme"
      >
        <Sun v-if="theme === 'dark'" :size="16" aria-hidden="true" />
        <Moon v-else :size="16" aria-hidden="true" />
      </button>
      <button v-if="user" class="btn ghost sm icon-command" title="退出登录" @click="logout">
        <LogOut :size="16" aria-hidden="true" />
        <span class="sr-only">退出登录</span>
      </button>
    </header>
    <main v-if="user" class="content">
      <div class="container">
        <RouterView />
      </div>
    </main>
    <main v-else class="content auth-page">
      <form class="card auth-card" @submit.prevent="login">
        <div class="auth-logo"><BookOpen :size="22" aria-hidden="true" /></div>
        <h1>登录 Enterprise RAG</h1>
        <p class="muted">使用租户账号进入企业知识库。所有知识库、文档、会话都会按租户隔离。</p>
        <div v-if="loginError" class="state-banner error">{{ loginError }}</div>
        <div class="field">
          <label for="login-tenant">租户标识</label>
          <input
            id="login-tenant"
            v-model="tenantSlug"
            class="input"
            type="text"
            autocomplete="organization"
            maxlength="64"
            required
            :aria-invalid="Boolean(loginError)"
          />
        </div>
        <div class="field">
          <label for="login-email">邮箱</label>
          <input
            id="login-email"
            v-model="email"
            class="input"
            type="email"
            autocomplete="username"
            required
            :aria-invalid="Boolean(loginError)"
          />
        </div>
        <div class="field">
          <label for="login-password">密码</label>
          <input
            id="login-password"
            v-model="password"
            class="input"
            type="password"
            autocomplete="current-password"
            required
            :aria-invalid="Boolean(loginError)"
          />
        </div>
        <button
          class="btn primary auth-submit"
          type="submit"
          :disabled="loggingIn || !tenantSlug.trim() || !email.trim() || !password"
        >
          <span v-if="loggingIn" class="spin"></span> 登录
        </button>
        <button v-if="isMockMode" class="btn ghost demo-account" type="button" @click="fillDemoAccount">
          填入本地演示账号
        </button>
      </form>
    </main>
    <Transition name="fade">
      <div v-if="message" class="toast" role="status" aria-live="polite">{{ message }}</div>
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
