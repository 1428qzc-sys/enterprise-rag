<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import {
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ScrollText,
  ShieldCheck,
  Trash2,
  UserCheck,
  Users,
  X,
} from "@lucide/vue";
import { adminApi } from "@/api/client";
import type { AuditLogItem, AuthUser, PermissionInfo, RoleInfo } from "@/api/types";
import { useToast } from "@/composables/toast";
import { useAuthStore } from "@/stores/auth";

type AdminTab = "users" | "roles" | "audit";

const { show } = useToast();
const auth = useAuthStore();
const authorized = computed(() => auth.can("admin:manage"));
const activeTab = ref<AdminTab>("users");
const users = ref<AuthUser[]>([]);
const roles = ref<RoleInfo[]>([]);
const permissions = ref<PermissionInfo[]>([]);
const auditLogs = ref<AuditLogItem[]>([]);
const loading = ref(true);
const refreshing = ref(false);
const error = ref("");
const saving = ref(false);
const pendingUserId = ref("");
const expandedAuditId = ref("");
const auditAction = ref("");
const auditOutcome = ref("");
const auditLoading = ref(false);
const auditHasMore = ref(false);
const AUDIT_PAGE_SIZE = 50;

const userModalOpen = ref(false);
const editingUserId = ref("");
const userEmailInput = ref<HTMLInputElement | null>(null);
const userForm = reactive({
  email: "",
  display_name: "",
  password: "",
  is_active: true,
  role_ids: [] as string[],
});

const roleModalOpen = ref(false);
const editingRoleId = ref("");
const roleNameInput = ref<HTMLInputElement | null>(null);
const roleForm = reactive({
  name: "",
  description: "",
  permissions: [] as string[],
});

function errorMessage(value: unknown, fallback: string): string {
  const response = value as { response?: { status?: number; data?: { detail?: string } } };
  if (response.response?.status === 403) return "当前账号没有租户管理权限";
  return response.response?.data?.detail || fallback;
}

async function loadAudit(reset = true) {
  if (auditLoading.value) return;
  auditLoading.value = true;
  try {
    const next = await adminApi.auditLogs({
      action: auditAction.value.trim() || undefined,
      outcome: auditOutcome.value || undefined,
      offset: reset ? 0 : auditLogs.value.length,
      limit: AUDIT_PAGE_SIZE,
    });
    auditLogs.value = reset ? next : [...auditLogs.value, ...next];
    auditHasMore.value = next.length === AUDIT_PAGE_SIZE;
  } finally {
    auditLoading.value = false;
  }
}

async function applyAuditFilters() {
  try {
    await loadAudit(true);
  } catch (value) {
    show(errorMessage(value, "审计记录加载失败"));
  }
}

async function loadMoreAudit() {
  try {
    await loadAudit(false);
  } catch (value) {
    show(errorMessage(value, "更多审计记录加载失败"));
  }
}

async function loadAll() {
  refreshing.value = true;
  error.value = "";
  try {
    const [nextUsers, nextRoles, nextPermissions] = await Promise.all([
      adminApi.users(),
      adminApi.roles(),
      adminApi.permissions(),
    ]);
    users.value = nextUsers;
    roles.value = nextRoles;
    permissions.value = nextPermissions;
    await loadAudit(true);
  } catch (value) {
    error.value = errorMessage(value, "租户管理数据加载失败");
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

function openCreateUser() {
  editingUserId.value = "";
  Object.assign(userForm, {
    email: "",
    display_name: "",
    password: "",
    is_active: true,
    role_ids: roles.value.find((role) => role.name === "member")?.id
      ? [roles.value.find((role) => role.name === "member")!.id]
      : [],
  });
  userModalOpen.value = true;
  nextTick(() => userEmailInput.value?.focus());
}

function openEditUser(user: AuthUser) {
  editingUserId.value = user.id;
  Object.assign(userForm, {
    email: user.email,
    display_name: user.display_name,
    password: "",
    is_active: user.is_active,
    role_ids: [...user.role_ids],
  });
  userModalOpen.value = true;
}

function closeUserModal() {
  if (!saving.value) userModalOpen.value = false;
}

async function saveUser() {
  if (!userForm.email.trim() || (!editingUserId.value && userForm.password.length < 8)) return;
  saving.value = true;
  try {
    if (editingUserId.value) {
      const body: {
        display_name: string;
        is_active: boolean;
        role_ids: string[];
        password?: string;
      } = {
        display_name: userForm.display_name.trim(),
        is_active: userForm.is_active,
        role_ids: [...userForm.role_ids],
      };
      if (userForm.password) body.password = userForm.password;
      await adminApi.updateUser(editingUserId.value, body);
      show("用户设置已保存");
    } else {
      await adminApi.createUser({
        email: userForm.email.trim(),
        password: userForm.password,
        display_name: userForm.display_name.trim(),
        is_active: userForm.is_active,
        role_ids: [...userForm.role_ids],
      });
      show("用户已创建");
    }
    userModalOpen.value = false;
    users.value = await adminApi.users();
    await loadAudit(true);
  } catch (value) {
    show(errorMessage(value, "用户保存失败"));
  } finally {
    saving.value = false;
  }
}

async function toggleUser(user: AuthUser) {
  pendingUserId.value = user.id;
  try {
    await adminApi.updateUser(user.id, { is_active: !user.is_active });
    users.value = await adminApi.users();
    show(user.is_active ? "用户已停用" : "用户已启用");
  } catch (value) {
    show(errorMessage(value, "用户状态更新失败"));
  } finally {
    pendingUserId.value = "";
  }
}

function openCreateRole() {
  editingRoleId.value = "";
  Object.assign(roleForm, { name: "", description: "", permissions: [] });
  roleModalOpen.value = true;
  nextTick(() => roleNameInput.value?.focus());
}

function openEditRole(role: RoleInfo) {
  editingRoleId.value = role.id;
  Object.assign(roleForm, {
    name: role.name,
    description: role.description,
    permissions: [...role.permissions],
  });
  roleModalOpen.value = true;
}

function closeRoleModal() {
  if (!saving.value) roleModalOpen.value = false;
}

async function saveRole() {
  if (!roleForm.name.trim()) return;
  saving.value = true;
  try {
    const body = {
      name: roleForm.name.trim(),
      description: roleForm.description.trim(),
      permissions: [...roleForm.permissions],
    };
    if (editingRoleId.value) {
      await adminApi.updateRole(editingRoleId.value, body);
      show("角色已更新");
    } else {
      await adminApi.createRole(body);
      show("角色已创建");
    }
    roleModalOpen.value = false;
    [roles.value, users.value] = await Promise.all([adminApi.roles(), adminApi.users()]);
    await loadAudit(true);
  } catch (value) {
    show(errorMessage(value, "角色保存失败"));
  } finally {
    saving.value = false;
  }
}

async function removeRole(role: RoleInfo) {
  if (!confirm(`确认删除角色「${role.name}」？`)) return;
  try {
    await adminApi.removeRole(role.id);
    roles.value = await adminApi.roles();
    show("角色已删除");
    await loadAudit(true);
  } catch (value) {
    show(errorMessage(value, "角色删除失败"));
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function outcomeLabel(outcome: string): string {
  return { success: "成功", accepted: "已受理", denied: "已拒绝", failure: "失败" }[outcome] || outcome;
}

onMounted(() => {
  if (authorized.value) void loadAll();
  else loading.value = false;
});
</script>

<template>
  <div class="admin-page">
    <section v-if="!authorized" class="card permission-denied" role="status">
      <ShieldCheck :size="30" aria-hidden="true" />
      <h1>无权访问租户管理</h1>
      <p>当前账号只能使用已分配的知识库功能，不能查看或修改用户、角色和审计记录。</p>
      <RouterLink class="btn primary" to="/">返回工作台</RouterLink>
    </section>

    <template v-else>
    <div class="page-head">
      <div>
        <h1>租户管理</h1>
        <p>维护当前租户的用户、角色权限与审计记录。</p>
      </div>
      <button class="btn icon-command" type="button" title="刷新" :disabled="refreshing" @click="loadAll">
        <RefreshCw :size="16" :class="{ rotating: refreshing }" aria-hidden="true" />
        <span class="sr-only">刷新</span>
      </button>
    </div>

    <div v-if="error" class="state-banner error" role="alert">
      {{ error }}
      <button class="btn sm" type="button" @click="loadAll">重试</button>
    </div>

    <div class="admin-tabs" role="tablist" aria-label="租户管理视图">
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'users'"
        :class="{ active: activeTab === 'users' }"
        @click="activeTab = 'users'"
      >
        <Users :size="16" aria-hidden="true" /> 用户
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'roles'"
        :class="{ active: activeTab === 'roles' }"
        @click="activeTab = 'roles'"
      >
        <ShieldCheck :size="16" aria-hidden="true" /> 角色与权限
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'audit'"
        :class="{ active: activeTab === 'audit' }"
        @click="activeTab = 'audit'"
      >
        <ScrollText :size="16" aria-hidden="true" /> 审计记录
      </button>
    </div>

    <section class="admin-panel" :aria-busy="loading">
      <div v-if="loading" class="state-banner loading"><span class="spin"></span> 加载租户数据…</div>

      <template v-else-if="activeTab === 'users'">
        <div class="panel-toolbar">
          <div>
            <h2>用户</h2>
            <span class="muted">{{ users.length }} 个账号</span>
          </div>
          <button class="btn primary" type="button" @click="openCreateUser">
            <Plus :size="16" aria-hidden="true" /> 新建用户
          </button>
        </div>
        <div v-if="users.length === 0" class="empty compact">暂无用户</div>
        <div v-else class="table-wrap admin-table-wrap">
          <table class="table admin-table">
            <thead>
              <tr><th>用户</th><th>角色</th><th>权限</th><th>状态</th><th class="actions-cell">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.id">
                <td data-label="用户">
                  <strong>{{ user.display_name || user.email }}</strong>
                  <span class="cell-subtext">{{ user.email }}</span>
                </td>
                <td data-label="角色">
                  <div class="chip-list">
                    <span v-for="name in user.role_names" :key="name" class="chip">{{ name }}</span>
                    <span v-if="user.role_names.length === 0" class="muted">未分配</span>
                  </div>
                </td>
                <td data-label="权限">{{ user.permissions.length }} 项</td>
                <td data-label="状态">
                  <span class="status-dot" :class="user.is_active ? 'active' : 'inactive'">
                    {{ user.is_active ? "启用" : "停用" }}
                  </span>
                </td>
                <td class="actions-cell" data-label="操作">
                  <button class="btn ghost sm icon-command" type="button" title="编辑用户" @click="openEditUser(user)">
                    <Pencil :size="15" aria-hidden="true" /><span class="sr-only">编辑用户</span>
                  </button>
                  <button
                    class="btn ghost sm icon-command"
                    type="button"
                    :title="user.is_active ? '停用用户' : '启用用户'"
                    :disabled="pendingUserId === user.id"
                    @click="toggleUser(user)"
                  >
                    <Ban v-if="user.is_active" :size="15" aria-hidden="true" />
                    <UserCheck v-else :size="15" aria-hidden="true" />
                    <span class="sr-only">{{ user.is_active ? "停用用户" : "启用用户" }}</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else-if="activeTab === 'roles'">
        <div class="panel-toolbar">
          <div><h2>角色与权限</h2><span class="muted">{{ roles.length }} 个角色</span></div>
          <button class="btn primary" type="button" @click="openCreateRole">
            <Plus :size="16" aria-hidden="true" /> 新建角色
          </button>
        </div>
        <div v-if="roles.length === 0" class="empty compact">暂无角色</div>
        <div v-else class="table-wrap admin-table-wrap">
          <table class="table admin-table">
            <thead><tr><th>角色</th><th>权限</th><th>用户</th><th class="actions-cell">操作</th></tr></thead>
            <tbody>
              <tr v-for="role in roles" :key="role.id">
                <td data-label="角色">
                  <strong>{{ role.name }}</strong>
                  <span class="cell-subtext">{{ role.description || "无描述" }}</span>
                </td>
                <td data-label="权限">
                  <div class="chip-list">
                    <span v-for="code in role.permissions" :key="code" class="chip mono">{{ code }}</span>
                    <span v-if="role.permissions.length === 0" class="muted">无权限</span>
                  </div>
                </td>
                <td data-label="用户">{{ role.user_count }}</td>
                <td class="actions-cell" data-label="操作">
                  <button class="btn ghost sm icon-command" type="button" title="编辑角色" @click="openEditRole(role)">
                    <Pencil :size="15" aria-hidden="true" /><span class="sr-only">编辑角色</span>
                  </button>
                  <button
                    class="btn ghost sm icon-command danger-text"
                    type="button"
                    title="删除角色"
                    :disabled="role.is_system"
                    @click="removeRole(role)"
                  >
                    <Trash2 :size="15" aria-hidden="true" /><span class="sr-only">删除角色</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else>
        <div class="panel-toolbar audit-toolbar">
          <div><h2>审计记录</h2><span class="muted">最近 {{ auditLogs.length }} 条</span></div>
          <div class="audit-filters">
            <input v-model="auditAction" class="input" placeholder="操作代码（精确匹配）" @keyup.enter="applyAuditFilters" />
            <select v-model="auditOutcome" class="input" aria-label="结果筛选">
              <option value="">全部结果</option><option value="success">成功</option>
              <option value="accepted">已受理</option><option value="denied">已拒绝</option>
              <option value="failure">失败</option>
            </select>
            <button class="btn" type="button" :disabled="auditLoading" @click="applyAuditFilters">
              <span v-if="auditLoading" class="spin"></span> 筛选
            </button>
          </div>
        </div>
        <div v-if="auditLogs.length === 0" class="empty compact">没有符合条件的审计记录</div>
        <div v-else class="audit-list">
          <button
            v-for="log in auditLogs"
            :key="log.id"
            type="button"
            class="audit-row"
            :aria-expanded="expandedAuditId === log.id"
            @click="expandedAuditId = expandedAuditId === log.id ? '' : log.id"
          >
            <ChevronDown v-if="expandedAuditId === log.id" :size="15" aria-hidden="true" />
            <ChevronRight v-else :size="15" aria-hidden="true" />
            <span class="mono audit-action">{{ log.action }}</span>
            <span class="status-dot" :class="log.outcome">{{ outcomeLabel(log.outcome) }}</span>
            <span class="audit-resource">{{ log.resource_type || "system" }} {{ log.resource_id }}</span>
            <time>{{ formatTime(log.created_at) }}</time>
            <span v-if="expandedAuditId === log.id" class="audit-detail">
              <span>用户：{{ log.user_id || "匿名" }} · IP：{{ log.ip_address || "未记录" }}</span>
              <pre>{{ JSON.stringify(log.detail, null, 2) }}</pre>
            </span>
          </button>
          <div v-if="auditHasMore" class="audit-load-more">
            <button class="btn" type="button" :disabled="auditLoading" @click="loadMoreAudit">
              <span v-if="auditLoading" class="spin"></span>
              {{ auditLoading ? "加载中" : "加载更多" }}
            </button>
          </div>
        </div>
      </template>
    </section>

    <div v-if="userModalOpen" class="modal-mask" @click.self="closeUserModal" @keydown.esc="closeUserModal">
      <form
        class="card modal admin-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-dialog-title"
        @submit.prevent="saveUser"
      >
        <div class="modal-title-row">
          <h3 id="user-dialog-title">{{ editingUserId ? "编辑用户" : "新建用户" }}</h3>
          <button class="btn ghost sm icon-command" type="button" title="关闭" :disabled="saving" @click="closeUserModal">
            <X :size="16" aria-hidden="true" /><span class="sr-only">关闭</span>
          </button>
        </div>
        <div class="field"><label for="admin-user-email">邮箱</label>
          <input id="admin-user-email" ref="userEmailInput" v-model="userForm.email" class="input" type="email" :disabled="Boolean(editingUserId)" />
        </div>
        <div class="field"><label for="admin-user-name">显示名称</label>
          <input id="admin-user-name" v-model="userForm.display_name" class="input" maxlength="64" />
        </div>
        <div class="field"><label for="admin-user-password">{{ editingUserId ? "新密码（留空不修改）" : "初始密码" }}</label>
          <input id="admin-user-password" v-model="userForm.password" class="input" type="password" autocomplete="new-password" />
        </div>
        <fieldset class="field option-fieldset"><legend>角色</legend>
          <label v-for="role in roles" :key="role.id" class="check-option">
            <input v-model="userForm.role_ids" type="checkbox" :value="role.id" />
            <span><strong>{{ role.name }}</strong><small>{{ role.description || "无描述" }}</small></span>
          </label>
        </fieldset>
        <label class="toggle-row"><input v-model="userForm.is_active" type="checkbox" /> <span>账号启用</span></label>
        <div class="modal-actions">
          <button class="btn" type="button" :disabled="saving" @click="closeUserModal">取消</button>
          <button class="btn primary" type="submit" :disabled="saving || !userForm.email.trim() || (!editingUserId && userForm.password.length < 8)">
            <Save :size="16" aria-hidden="true" /> 保存
          </button>
        </div>
      </form>
    </div>

    <div v-if="roleModalOpen" class="modal-mask" @click.self="closeRoleModal" @keydown.esc="closeRoleModal">
      <form
        class="card modal admin-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="role-dialog-title"
        @submit.prevent="saveRole"
      >
        <div class="modal-title-row">
          <h3 id="role-dialog-title">{{ editingRoleId ? "编辑角色" : "新建角色" }}</h3>
          <button class="btn ghost sm icon-command" type="button" title="关闭" :disabled="saving" @click="closeRoleModal">
            <X :size="16" aria-hidden="true" /><span class="sr-only">关闭</span>
          </button>
        </div>
        <div class="field"><label for="admin-role-name">角色名</label>
          <input id="admin-role-name" ref="roleNameInput" v-model="roleForm.name" class="input" maxlength="64" />
        </div>
        <div class="field"><label for="admin-role-description">描述</label>
          <input id="admin-role-description" v-model="roleForm.description" class="input" maxlength="256" />
        </div>
        <fieldset class="field option-fieldset"><legend>权限</legend>
          <label v-for="permission in permissions" :key="permission.code" class="check-option">
            <input v-model="roleForm.permissions" type="checkbox" :value="permission.code" />
            <span><strong class="mono">{{ permission.code }}</strong><small>{{ permission.description }}</small></span>
          </label>
        </fieldset>
        <div class="modal-actions">
          <button class="btn" type="button" :disabled="saving" @click="closeRoleModal">取消</button>
          <button class="btn primary" type="submit" :disabled="saving || !roleForm.name.trim()">
            <Check :size="16" aria-hidden="true" /> 保存
          </button>
        </div>
      </form>
    </div>
    </template>
  </div>
</template>

<style scoped>
.permission-denied {
  min-height: 320px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  text-align: center;
}
.permission-denied svg {
  color: var(--warning);
}
.permission-denied h1,
.permission-denied p {
  margin: 0;
}
.permission-denied p {
  max-width: 540px;
  color: var(--muted);
}

@media (max-width: 600px) {
  .admin-page .page-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 40px;
    align-items: start;
  }
  .admin-page .page-head > .icon-command {
    align-self: start;
  }
}
</style>
