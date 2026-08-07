<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Download,
  MessageSquareText,
  Plus,
  RotateCw,
  Send,
  Square,
  Trash2,
  UserRound,
  WifiOff,
} from "@lucide/vue";
import {
  apiErrorMessage,
  chatApi,
  createRequestId,
  streamChat,
  StreamChatError,
} from "@/api/client";
import type { ChatMessage, Conversation, SourceChunk } from "@/api/types";
import KbTabs from "@/components/KbTabs.vue";
import { useToast } from "@/composables/toast";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const kbId = route.params.id as string;
const { show } = useToast();
const auth = useAuthStore();

const conversations = ref<Conversation[]>([]);
const currentConvId = ref<string | null>(null);
const messages = ref<ChatMessage[]>([]);
const input = ref("");
const streaming = ref(false);
const loadingConversations = ref(true);
const conversationError = ref("");
const messageError = ref("");
const loadingMessages = ref(false);
const exporting = ref(false);
const messagesEl = ref<HTMLElement | null>(null);
const sourcesOpen = ref<Record<number, boolean>>({});
const online = ref(typeof navigator === "undefined" ? true : navigator.onLine);
const retryTurn = ref<{
  question: string;
  requestId: string;
  assistant: ChatMessage;
} | null>(null);
let controller: AbortController | null = null;
let activeRequestId = "";
let intentionallyStopped = false;
let disposed = false;
let messageLoadGeneration = 0;

function scoreLabel(source: SourceChunk) {
  if (source.rerank_score !== null) return `重排 ${source.rerank_score.toFixed(3)}`;
  return `RRF ${source.rrf_score.toFixed(4)}`;
}

function toggleSources(index: number) {
  sourcesOpen.value[index] = !isSourcesOpen(index);
}

function isSourcesOpen(index: number) {
  return sourcesOpen.value[index] !== false;
}

function openSource(source: SourceChunk) {
  router.push({
    path: `/kb/${kbId}/documents`,
    query: { document: source.document_id, chunk: source.chunk_id },
  });
}

function diagnosticsLabel(message: ChatMessage) {
  const diagnostics = message.diagnostics;
  if (!diagnostics) return "";
  if (diagnostics.cached) return "已从幂等回合缓存恢复";
  const parts = [
    diagnostics.total_ms !== undefined ? `检索 ${diagnostics.total_ms.toFixed(1)} ms` : "",
    diagnostics.rerank?.provider ? `重排 ${diagnostics.rerank.provider}` : "",
    diagnostics.degraded ? "已降级" : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

async function loadConversations() {
  loadingConversations.value = true;
  conversationError.value = "";
  try {
    conversations.value = await chatApi.conversations(kbId);
  } catch (error: unknown) {
    conversationError.value = apiErrorMessage(error, "会话列表加载失败");
  } finally {
    loadingConversations.value = false;
  }
}

async function selectConversation(id: string) {
  if (streaming.value) stop();
  const generation = ++messageLoadGeneration;
  loadingMessages.value = true;
  messageError.value = "";
  try {
    currentConvId.value = id;
    const loadedMessages = await chatApi.messages(id);
    if (
      disposed ||
      generation !== messageLoadGeneration ||
      currentConvId.value !== id ||
      streaming.value
    ) {
      return;
    }
    messages.value = loadedMessages;
    retryTurn.value = null;
    scrollBottom();
  } catch (error: unknown) {
    if (!disposed && generation === messageLoadGeneration) {
      messageError.value = apiErrorMessage(error, "会话消息加载失败");
    }
  } finally {
    if (generation === messageLoadGeneration) loadingMessages.value = false;
  }
}

function newConversation() {
  if (streaming.value) stop();
  messageLoadGeneration += 1;
  loadingMessages.value = false;
  currentConvId.value = null;
  messages.value = [];
  messageError.value = "";
  retryTurn.value = null;
}

async function removeConversation(id: string, ev: Event) {
  ev.stopPropagation();
  if (!confirm("删除该对话？")) return;
  try {
    await chatApi.removeConversation(id);
    if (currentConvId.value === id) newConversation();
    await loadConversations();
    show("会话已删除");
  } catch (error: unknown) {
    show(apiErrorMessage(error, "删除会话失败"));
  }
}

function scrollBottom() {
  nextTick(() => {
    requestAnimationFrame(() => {
      if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    });
  });
}

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : (error as { name?: string })?.name === "AbortError";
}

async function exportConversation() {
  if (!currentConvId.value || exporting.value) return;
  exporting.value = true;
  try {
    const blob = await chatApi.exportMarkdown(currentConvId.value);
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `conversation-${currentConvId.value}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
    show("Markdown 已导出");
  } catch (error: unknown) {
    show(apiErrorMessage(error, "导出对话失败"));
  } finally {
    exporting.value = false;
  }
}

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function executeTurn(question: string, requestId: string, assistant: ChatMessage) {
  activeRequestId = requestId;
  intentionallyStopped = false;
  streaming.value = true;
  assistant.streaming = true;
  assistant.error = "";
  assistant.retryable = false;
  retryTurn.value = { question, requestId, assistant };

  try {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (attempt > 0) {
        assistant.content = "";
        assistant.sources = [];
        assistant.error = "连接中断，正在使用同一回合重新连接…";
        await delay(500);
        if (intentionallyStopped || disposed) return;
      }
      controller = new AbortController();
      try {
        await streamChat(
          {
            kb_id: kbId,
            question,
            conversation_id: currentConvId.value,
            request_id: requestId,
          },
          {
            onMeta: (data) => {
              if (activeRequestId !== requestId) return;
              const isNew = !currentConvId.value;
              currentConvId.value = data.conversation_id;
              assistant.request_id = data.request_id;
              assistant.diagnostics = data.diagnostics;
              if (isNew) void loadConversations();
            },
            onSources: (sources) => {
              if (activeRequestId !== requestId) return;
              assistant.sources = sources;
              scrollBottom();
            },
            onToken: (text) => {
              if (activeRequestId !== requestId) return;
              assistant.error = "";
              assistant.content += text;
              scrollBottom();
            },
            onReplace: (data) => {
              if (activeRequestId !== requestId) return;
              assistant.content = data.answer;
              assistant.sources = data.sources;
            },
            onDone: (data) => {
              if (activeRequestId !== requestId) return;
              assistant.id = data.message_id;
              assistant.request_id = data.request_id;
              assistant.content = data.answer;
              assistant.sources = data.sources;
              assistant.diagnostics = data.diagnostics;
              assistant.error = "";
              assistant.retryable = false;
            },
          },
          controller.signal,
        );
        retryTurn.value = null;
        await loadConversations();
        return;
      } catch (error) {
        if (isAbortError(error)) {
          assistant.error = intentionallyStopped ? "已停止生成" : "连接已取消";
          assistant.retryable = true;
          return;
        }
        const normalized =
          error instanceof StreamChatError
            ? error
            : new StreamChatError("网络连接失败", 0, true);
        if (attempt === 0 && normalized.retryable && online.value) continue;
        assistant.error = normalized.message;
        assistant.retryable = normalized.retryable;
        show(normalized.message);
        return;
      }
    }
  } finally {
    if (activeRequestId === requestId) {
      assistant.streaming = false;
      streaming.value = false;
      controller = null;
    }
  }
}

async function send() {
  const question = input.value.trim();
  if (
    !question ||
    streaming.value ||
    loadingConversations.value ||
    loadingMessages.value ||
    !online.value
  ) {
    return;
  }
  messageLoadGeneration += 1;
  input.value = "";
  const requestId = createRequestId();
  messages.value.push({ role: "user", content: question, request_id: requestId });
  const assistant: ChatMessage = {
    role: "assistant",
    content: "",
    sources: [],
    streaming: true,
    request_id: requestId,
  };
  messages.value.push(assistant);
  scrollBottom();
  await executeTurn(question, requestId, assistant);
}

async function retryLast() {
  if (!retryTurn.value || streaming.value || !online.value) return;
  const { question, requestId, assistant } = retryTurn.value;
  assistant.content = "";
  assistant.sources = [];
  await executeTurn(question, requestId, assistant);
}

function stop() {
  intentionallyStopped = true;
  controller?.abort();
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

onMounted(async () => {
  window.addEventListener("online", updateOnline);
  window.addEventListener("offline", updateOnline);
  await loadConversations();
  if (conversations.value.length > 0) await selectConversation(conversations.value[0].id);
});

function updateOnline() {
  online.value = navigator.onLine;
}

onBeforeUnmount(() => {
  disposed = true;
  messageLoadGeneration += 1;
  intentionallyStopped = true;
  controller?.abort();
  window.removeEventListener("online", updateOnline);
  window.removeEventListener("offline", updateOnline);
});
</script>

<template>
  <div>
    <KbTabs :kb-id="kbId" active="chat" />

    <div v-if="!online" class="state-banner error" role="status">
      <WifiOff :size="17" aria-hidden="true" />
      当前离线，恢复网络后可继续发送或重连未完成问答。
    </div>

    <div class="chat-layout">
      <div class="card conv-list">
        <div class="conversation-toolbar">
          <button class="btn primary sm" type="button" @click="newConversation">
            <Plus :size="14" aria-hidden="true" /> 新对话
          </button>
          <button
            class="btn ghost sm"
            type="button"
            :disabled="!currentConvId || exporting"
            @click="exportConversation"
          >
            <Download :size="14" aria-hidden="true" />
            {{ exporting ? "导出中" : "导出" }}
          </button>
        </div>
        <div v-if="conversationError" class="conversation-error" role="alert">
          <span>{{ conversationError }}</span>
          <button class="btn ghost sm icon-command" type="button" title="重试" @click="loadConversations">
            <RotateCw :size="14" aria-hidden="true" /><span class="sr-only">重试加载会话</span>
          </button>
        </div>
        <div v-if="loadingConversations" class="conv-skeleton" aria-busy="true">
          <div v-for="n in 3" :key="n" class="conv-skeleton-row"></div>
        </div>
        <template v-else>
          <div
            v-for="c in conversations"
            :key="c.id"
            class="conv-item-row"
            :class="{
              active: c.id === currentConvId,
              readonly: !auth.can('conversation:delete'),
            }"
          >
            <button class="conv-select" type="button" @click="selectConversation(c.id)">
              <MessageSquareText :size="14" aria-hidden="true" />
              <span>{{ c.title }}</span>
            </button>
            <button
              v-if="auth.can('conversation:delete')"
              class="conv-delete"
              type="button"
              :aria-label="`删除会话 ${c.title}`"
              @click="removeConversation(c.id, $event)"
            >
              <Trash2 :size="13" aria-hidden="true" />
            </button>
          </div>
          <div v-if="conversations.length === 0" class="muted" style="font-size: 12px; padding: 8px">
            暂无历史对话
          </div>
        </template>
      </div>

      <div class="card chat-main">
        <div ref="messagesEl" class="messages">
          <div v-if="messageError" class="state-banner error" role="alert">
            {{ messageError }}
            <button v-if="currentConvId" class="btn sm" type="button" @click="selectConversation(currentConvId)">
              重试
            </button>
          </div>
          <div v-else-if="loadingMessages" class="message-loading" aria-busy="true">
            <span class="spin"></span> 加载会话消息…
          </div>
          <div v-else-if="messages.length === 0" class="empty chat-empty">
            <MessageSquareText class="chat-empty-icon" :size="34" aria-hidden="true" />
            <h2>从可信资料中找到答案</h2>
            <p>向知识库提问，回答会基于检索到的文档并标注引用来源。</p>
            <ul class="chat-empty-tips">
              <li>Enter 发送 · Shift+Enter 换行</li>
              <li>回答下方可查看真实引用片段与检索信号</li>
            </ul>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="avatar">
              <UserRound v-if="m.role === 'user'" :size="16" aria-hidden="true" />
              <Bot v-else :size="16" aria-hidden="true" />
            </div>
            <div style="flex: 1; min-width: 0">
              <div class="bubble">
                <span>{{ m.content }}</span>
                <span v-if="m.streaming && !m.content" class="typing-dots" aria-label="生成中">
                  <span></span><span></span><span></span>
                </span>
                <span v-else-if="m.streaming" class="spin" style="margin-left: 6px"></span>
              </div>

              <div v-if="m.error" class="message-error" role="status">
                <span>{{ m.error }}</span>
                <button
                  v-if="m.retryable"
                  class="btn sm"
                  type="button"
                  :disabled="streaming || !online"
                  @click="retryLast"
                >
                  重新连接
                </button>
              </div>

              <div v-if="m.role === 'assistant' && diagnosticsLabel(m)" class="retrieval-diagnostics">
                {{ diagnosticsLabel(m) }}
              </div>

              <div
                v-if="m.role === 'assistant' && m.streaming && (!m.sources || !m.sources.length)"
                class="sources sources-skeleton"
                aria-busy="true"
              >
                <div class="source-skeleton-row"></div>
                <div class="source-skeleton-row short"></div>
              </div>

              <div v-else-if="m.sources && m.sources.length" class="sources">
                <button
                  type="button"
                  class="sources-toggle"
                  :aria-expanded="isSourcesOpen(i)"
                  :aria-controls="`sources-${i}`"
                  @click="toggleSources(i)"
                >
                  <span class="sources-toggle-label">引用来源 {{ m.sources.length }} 条</span>
                  <ChevronDown v-if="isSourcesOpen(i)" :size="15" aria-hidden="true" />
                  <ChevronRight v-else :size="15" aria-hidden="true" />
                </button>
                <div :id="`sources-${i}`" v-show="isSourcesOpen(i)" class="sources-list">
                  <div
                    v-for="s in m.sources"
                    :id="`source-${s.chunk_id}`"
                    :key="s.chunk_id"
                    class="source"
                  >
                    <div class="head">
                      <button
                        type="button"
                        class="cite"
                        title="在文档原文中定位"
                        @click="openSource(s)"
                      >
                        [{{ s.index }}]
                      </button>
                      <b>{{ s.document_name }}</b>
                      <span v-if="s.page">· 第 {{ s.page }} 页</span>
                      <span class="spacer" style="flex: 1"></span>
                      <span class="score-badge">{{ scoreLabel(s) }}</span>
                    </div>
                    <div class="snippet">{{ s.content }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="composer">
          <textarea
            v-model="input"
            class="textarea"
            rows="1"
            placeholder="输入问题"
            aria-label="问题"
            :disabled="streaming || loadingConversations || loadingMessages || !online"
            @keydown="onKey"
          ></textarea>
          <button v-if="streaming" class="btn danger composer-action" type="button" @click="stop">
            <Square :size="14" aria-hidden="true" /> 停止
          </button>
          <button
            v-else
            class="btn primary"
            :disabled="
              !input.trim() ||
              streaming ||
              loadingConversations ||
              loadingMessages ||
              !online
            "
            @click="send"
          >
            <Send :size="15" aria-hidden="true" /> 发送
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conversation-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.conversation-error {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px;
  border-radius: 7px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 11px;
}
.conversation-error span {
  flex: 1;
}
.conv-item-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 30px;
  align-items: center;
  border-radius: 8px;
  color: var(--muted);
}
.conv-item-row.readonly {
  grid-template-columns: minmax(0, 1fr);
}
.conv-item-row:hover {
  background: var(--panel-2);
}
.conv-item-row.active {
  background: var(--primary-soft);
  color: var(--primary-600);
}
.conv-select,
.conv-delete {
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.conv-select {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 6px 8px 9px;
  text-align: left;
}
.conv-select span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-delete {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  opacity: 0;
}
.conv-item-row:hover .conv-delete,
.conv-delete:focus-visible {
  opacity: 0.8;
}
.conv-delete:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.message-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
}
.chat-empty h2 {
  margin: 8px 0 0;
  color: var(--text);
  font-size: 16px;
}
.chat-empty p {
  margin: 4px 0;
}
.chat-empty-icon {
  color: var(--primary);
}
.message-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-top: 7px;
  padding: 7px 9px;
  border-left: 2px solid var(--danger);
  background: color-mix(in srgb, var(--danger) 7%, transparent);
  color: var(--danger);
  font-size: 11px;
}
.retrieval-diagnostics {
  margin-top: 6px;
  color: var(--muted);
  font-size: 10px;
}

@media (max-width: 760px) {
  .conv-delete {
    opacity: 0.75;
  }
  .conversation-toolbar {
    flex-wrap: nowrap;
    margin: 0;
  }
  .composer-action {
    min-width: 78px;
  }
}
</style>
