import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  { path: "/", name: "home", component: () => import("@/views/KnowledgeBases.vue") },
  { path: "/kb/:id", redirect: (to) => `/kb/${to.params.id}/chat` },
  {
    path: "/kb/:id/documents",
    name: "documents",
    component: () => import("@/views/Documents.vue"),
  },
  { path: "/kb/:id/chat", name: "chat", component: () => import("@/views/Chat.vue") },
  { path: "/admin", name: "admin", component: () => import("@/views/Admin.vue") },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
