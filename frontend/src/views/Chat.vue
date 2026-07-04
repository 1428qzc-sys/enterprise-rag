<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { chatApi, streamChat } from "@/api/client";
import type { ChatMessage, Conversation } from "@/api/types";
import KbTabs from "@/components/KbTabs.vue";
import { useToast } from "@/composables/toast";

const route = useRoute();
const kbId = route.params.id as string;
const { show } = useToast();

const conversations = ref<Conversation[]>([]);
const currentConvId = ref<string | null>(null);
const messages = ref<ChatMessage[]>([]);
const input = ref("");
const streaming = ref(false);
const messagesEl = ref<HTMLElement | null>(null);
let controller: AbortController | null = null;

async function loadConversations() {
  conversations.value = await chatApi.conversations(kbId);
}

async function selectConversation(id: string) {
  currentConvId.value = id;
  messages.value = await chatApi.messages(id);
  scrollBottom();
}

function newConversation() {
  currentConvId.value = null;
  messages.value = [];
}

async function removeConversation(id: string, ev: Event) {
  ev.stopPropagation();
  if (!confirm("删除该对话？")) return;
  await chatApi.removeConversation(id);
  if (currentConvId.value === id) newConversation();
  await loadConversations();
}

function scrollBottom() {
  nextTick(() => {
    requestAnimationFrame(() => {
      if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    });
  });
}

async function send() {
  const q = input.value.trim();
  if (!q || streaming.value) return;
  input.value = "";
  messages.value.push({ role: "user", content: q });
  const assistant: ChatMessage = { role: "assistant", content: "", sources: [], streaming: true };
  messages.value.push(assistant);
  streaming.value = true;
  scrollBottom();

  controller = new AbortController();
  await streamChat(
    { kb_id: kbId, question: q, conversation_id: currentConvId.value },
    {
      onMeta: (d) => {
        const isNew = !currentConvId.value;
        currentConvId.value = d.conversation_id;
        if (isNew) loadConversations();
      },
      onSources: (s) => {
        assistant.sources = s;
        scrollBottom();
      },
      onToken: (t) => {
        assistant.content += t;
        scrollBottom();
      },
      onDone: () => {
        assistant.streaming = false;
        streaming.value = false;
        loadConversations();
      },
      onError: (m) => {
        assistant.content += `\n\n⚠️ ${m}`;
        assistant.streaming = false;
        streaming.value = false;
        show(m);
      },
    },
    controller.signal,
  );
  streaming.value = false;
}

function stop() {
  controller?.abort();
  streaming.value = false;
  const last = messages.value[messages.value.length - 1];
  if (last && last.role === "assistant") last.streaming = false;
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

onMounted(async () => {
  await loadConversations();
  if (conversations.value.length > 0) await selectConversation(conversations.value[0].id);
});
</script>

<template>
  <div>
    <KbTabs :kb-id="kbId" active="chat" />

    <div class="chat-layout">
      <div class="card conv-list">
        <button class="btn primary sm" style="margin-bottom: 6px" @click="newConversation">
          + 新对话
        </button>
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === currentConvId }"
          @click="selectConversation(c.id)"
        >
          <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ c.title }}
          </span>
          <span class="x" @click="removeConversation(c.id, $event)">✕</span>
        </div>
        <div v-if="conversations.length === 0" class="muted" style="font-size: 12px; padding: 8px">
          暂无历史对话
        </div>
      </div>

      <div class="card chat-main">
        <div ref="messagesEl" class="messages">
          <div v-if="messages.length === 0" class="empty">
            <div class="big">💬</div>
            <p>向知识库提问，回答会基于检索到的文档并标注引用来源。</p>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="avatar">{{ m.role === "user" ? "🧑" : "🤖" }}</div>
            <div style="flex: 1; min-width: 0">
              <div class="bubble">
                <span>{{ m.content }}</span>
                <span v-if="m.streaming" class="spin" style="margin-left: 6px"></span>
              </div>

              <div v-if="m.sources && m.sources.length" class="sources">
                <div class="muted" style="font-size: 12px">引用来源 {{ m.sources.length }} 条</div>
                <div v-for="s in m.sources" :key="s.chunk_id" class="source">
                  <div class="head">
                    <span class="cite">[{{ s.index }}]</span>
                    <b>{{ s.document_name }}</b>
                    <span v-if="s.page">· 第 {{ s.page }} 页</span>
                    <span class="spacer" style="flex: 1"></span>
                    <span class="mono">score {{ s.score.toFixed(4) }}</span>
                  </div>
                  <div class="snippet">{{ s.content }}</div>
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
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            @keydown="onKey"
          ></textarea>
          <button v-if="streaming" class="btn danger" @click="stop">停止</button>
          <button v-else class="btn primary" :disabled="!input.trim()" @click="send">发送</button>
        </div>
      </div>
    </div>
  </div>
</template>
