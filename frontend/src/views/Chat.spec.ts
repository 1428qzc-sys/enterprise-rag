import { createApp, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Conversation } from "@/api/types";
import type { StreamHandlers } from "@/api/client";
import Chat from "./Chat.vue";

const clientMocks = vi.hoisted(() => ({
  conversations: vi.fn(),
  messages: vi.fn(),
  removeConversation: vi.fn(),
  exportMarkdown: vi.fn(),
  streamChat: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "kb-1" } }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/KbTabs.vue", () => ({
  default: { template: "<nav />" },
}));

vi.mock("@/composables/toast", () => ({
  useToast: () => ({ show: vi.fn() }),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({ can: () => true }),
}));

vi.mock("@/api/client", () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
  chatApi: {
    conversations: clientMocks.conversations,
    messages: clientMocks.messages,
    removeConversation: clientMocks.removeConversation,
    exportMarkdown: clientMocks.exportMarkdown,
  },
  createRequestId: () => "request-1",
  streamChat: clientMocks.streamChat,
  StreamChatError: class StreamChatError extends Error {
    constructor(
      message: string,
      readonly status = 0,
      readonly retryable = true,
    ) {
      super(message);
    }
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolver) => {
    resolve = resolver;
  });
  return { promise, resolve };
}

async function flushUi() {
  await Promise.resolve();
  await nextTick();
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  await nextTick();
}

describe("Chat conversation loading", () => {
  let app: ReturnType<typeof createApp> | null = null;
  let host: HTMLDivElement | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
  });

  afterEach(() => {
    app?.unmount();
    host?.remove();
    app = null;
    host = null;
    vi.unstubAllGlobals();
  });

  it("does not let a stale history response overwrite a newly submitted turn", async () => {
    const history = deferred<ChatMessage[]>();
    const conversations: Conversation[] = [
      {
        id: "conversation-1",
        kb_id: "kb-1",
        title: "已有会话",
        created_at: "2026-08-07T00:00:00Z",
        updated_at: "2026-08-07T00:00:00Z",
      },
    ];
    clientMocks.conversations.mockResolvedValue(conversations);
    clientMocks.messages.mockReturnValue(history.promise);
    clientMocks.streamChat.mockImplementation(
      async (_body: unknown, handlers: StreamHandlers) => {
        handlers.onMeta?.({
          conversation_id: "conversation-new",
          request_id: "request-1",
          used_query: "新问题",
          diagnostics: { total_ms: 1 },
        });
        handlers.onDone?.({
          message_id: "message-new",
          conversation_id: "conversation-new",
          request_id: "request-1",
          answer: "新回答 [1]",
          sources: [],
          diagnostics: { total_ms: 1 },
        });
      },
    );

    host = document.createElement("div");
    document.body.appendChild(host);
    app = createApp(Chat);
    app.mount(host);

    await vi.waitFor(() => {
      expect(clientMocks.messages).toHaveBeenCalledWith("conversation-1");
    });
    const textarea = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="问题"]');
    expect(textarea).not.toBeNull();
    expect(textarea!.disabled).toBe(true);

    const newConversationButton = Array.from(host.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("新对话"),
    );
    expect(newConversationButton).toBeDefined();
    newConversationButton!.click();
    await nextTick();
    expect(textarea!.disabled).toBe(false);

    textarea!.value = "新问题";
    textarea!.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    const sendButton = Array.from(host.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("发送"),
    );
    expect(sendButton).toBeDefined();
    sendButton!.click();

    await vi.waitFor(() => {
      expect(clientMocks.streamChat).toHaveBeenCalledOnce();
      expect(host!.querySelector(".messages")?.textContent).toContain("新回答 [1]");
    });

    history.resolve([{ role: "assistant", content: "过期回答" }]);
    await flushUi();

    const renderedMessages = host.querySelector(".messages")?.textContent || "";
    expect(renderedMessages).toContain("新问题");
    expect(renderedMessages).toContain("新回答 [1]");
    expect(renderedMessages).not.toContain("过期回答");
  });
});
